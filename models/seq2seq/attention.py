import torch
import torch.nn as nn
import torch.nn.functional as F
import math
from abc import ABC, abstractmethod

"""
This file contains implementations of various attention mechanisms for RNN-based seq2seq models.
Following attentions are implemented: global, local-m, local-p.
Following attention score functions are implemented: dot, general and concat.

These concepts were introduced in following papers:
**Neural Machine Translation by Jointly Learning to Align and Translate (Bahdanau et al., 2015)**
**Effective Approaches to Attention-based Neural Machine Translation (Luong et al., 2015)** 
"""

attention_map = {
    'global': lambda args, score: GlobalAttention(score),
    'local-m': lambda args, score: LocalMonotonicAttention(score, args.half_window_size),
    'local-p': lambda args, score: LocalPredictiveAttention(score, args.local_p_hidden_size, args.decoder_hidden_size,
                                                            args.half_window_size)
}

score_map = {
    'dot': lambda args: DotAttention(),
    'general': lambda args: GeneralAttention(encoder_hidden_size=args.encoder_hidden_size *
                                                                 (2 if args.encoder_bidirectional else 1),
                                             decoder_hidden_size=args.decoder_hidden_size),
    'concat': lambda args: ConcatAttention(hidden_size=args.concat_attention_hidden_size,
                                           encoder_hidden_size=args.encoder_hidden_size *
                                                               (2 if args.encoder_bidirectional else 1),
                                           decoder_hidden_size=args.decoder_hidden_size)
}


def attention_factory(args):
    """

    """
    score = score_map[args.attention_score](args)
    return attention_map[args.attention_type](args, score)


class Attention(ABC, nn.Module):
    """

    """

    def __init__(self, attn_score):
        super(Attention, self).__init__()
        self.attn_score = attn_score

    @abstractmethod
    def forward(self, t, hidden, encoder_outputs):
        raise NotImplemented

    def attn_weights(self, hidden, encoder_outputs):
        """

        """
        scores = self.attn_score(hidden, encoder_outputs)
        return F.softmax(scores, dim=1)

    def attn_context(self, attn_weights, encoder_outputs):
        """

        """
        weights = attn_weights.unsqueeze(2)  # (batch, seq_len) -> (batch, seq_len, 1)
        enc_out = encoder_outputs.permute(1, 2, 0)  # (seq_len, batch, enc_h) -> (batch, enc_h, seq_len)
        context = torch.bmm(enc_out, weights)  # (batch, enc_h, 1)
        return context.squeeze(2)


class GlobalAttention(Attention):
    """
 
    """

    def __init__(self, attn_score):
        super(GlobalAttention, self).__init__(attn_score)

    def forward(self, t, hidden, encoder_outputs):
        attn_weights = self.attn_weights(hidden, encoder_outputs)
        return attn_weights, self.attn_context(attn_weights, encoder_outputs)


class LocalMonotonicAttention(Attention):
    """

    """

    def __init__(self, attn_score, D):
        super(LocalMonotonicAttention, self).__init__(attn_score)
        self.D = D

    def forward(self, t, hidden, encoder_outputs):
        seq_len = encoder_outputs.size(0)
        # take fixed-size window around position t [t - D, t + D] of size 2D + 1
        enc_out = encoder_outputs[max(0, t - self.D):min(seq_len, t + self.D + 1)]

        attn_weights = self.attn_weights(hidden, enc_out)
        return attn_weights, self.attn_context(attn_weights, enc_out)


class LocalPredictiveAttention(Attention):
    """
    """

    def __init__(self, attn_score, hidden_size, decoder_hidden_size, D):
        super(LocalPredictiveAttention, self).__init__(attn_score)
        self.D = D
        self.Wp = nn.Linear(in_features=decoder_hidden_size, out_features=hidden_size)
        self.vp = nn.Linear(in_features=hidden_size, out_features=1)

    def forward(self, t, hidden, encoder_outputs):
        seq_len = encoder_outputs.size(0)

        # calculate p and local attn windows
        p = self.calculate_p(hidden, seq_len)
        window_indices, enc_out_local = self.slice_windows(encoder_outputs, p)

        # calculate attn weights and scaled them with gaussian distribution
        attn_weights = self.attn_weights(hidden, enc_out_local)
        attn_weights_scaled = self.scale_weights(window_indices, p, attn_weights)

        return attn_weights_scaled, self.attn_context(attn_weights_scaled, enc_out_local)

    def calculate_p(self, hidden, seq_len):
        """

        """
        Wph = torch.tanh(self.Wp(hidden))
        p = seq_len * F.sigmoid(self.vp(Wph))
        return p.squeeze(1)

    def slice_windows(self, encoder_outputs, p):
        """

        """
        batch_size = encoder_outputs.size(1)
        enc_hidden_size = encoder_outputs.size(2)
        window_size = 2 * self.D + 1

        # zero-pad encoder outputs
        start_pad = torch.zeros(self.D, batch_size, enc_hidden_size)
        end_pad = torch.zeros(self.D + 1, batch_size, enc_hidden_size)
        enc_out = torch.cat([start_pad, encoder_outputs, end_pad], dim=0)

        # calculate window indices
        idx1 = None
        for pt in p.detach():  # not sure if detach is needed
            center = round(pt.item())
            # window = (D + center - D, D + center + D) -> (center, center + 2D)
            # "D +" at the beginning because of padding
            window = torch.range(center, center + 2 * self.D, dtype=torch.long).unsqueeze(1)
            idx1 = window if idx1 is None else torch.cat([idx1, window], dim=1)

        window_indices = idx1  # (window_size, batch)
        idx1 = idx1.view(-1)  # flatten window indices
        idx2 = torch.tensor(list(range(batch_size)) * window_size, dtype=torch.long)  # batch dimension indices

        # TODO window_indices - D (undo padding) is potential problem, not sure how to handle this, should I clamp all
        # TODO values outside the window to 0 (with truncated gaussian)? Discuss this with Martin
        return (window_indices - self.D).float(), enc_out[idx1, idx2].view(window_size, batch_size, -1)

    def scale_weights(self, window_indices, p, attn_weights):
        """

        """
        stddev = self.D / 2
        numerator = (window_indices - p.unsqueeze(0)) ** 2  # (window_size, batch)
        gauss = torch.exp(-(1. / (2 * stddev ** 2)) * numerator)  # (window_size, batch)
        return attn_weights * gauss.t()


class AttentionScore(ABC, nn.Module):
    """

    """

    @abstractmethod
    def forward(self, hidden, encoder_outputs):
        raise NotImplemented


class DotAttention(AttentionScore):
    """

    """

    def forward(self, hidden, encoder_outputs):
        assert hidden.size(1) == encoder_outputs.size(2)
        hidden = hidden.unsqueeze(1)  # (batch, h) -> (batch, 1, h)
        enc_out = encoder_outputs.permute(1, 2, 0)  # (seq_len, batch, h) -> (batch, h, seq_len)
        scores = torch.bmm(hidden, enc_out)  # (batch, 1, seq_len)
        return scores.squeeze(1)


class GeneralAttention(AttentionScore):
    """
 
    """

    def __init__(self, encoder_hidden_size, decoder_hidden_size):
        super(GeneralAttention, self).__init__()
        # TODO is this the right way to init this parameter?
        data = torch.Tensor(decoder_hidden_size, encoder_hidden_size)
        stdev = 1. / math.sqrt(decoder_hidden_size)
        data.normal_(-stdev, stdev)
        self.W = nn.Parameter(data)

    def forward(self, hidden, encoder_outputs):
        hW = hidden.mm(self.W)  # (batch, enc_h)
        hW = hW.unsqueeze(1)  # (batch, enc_h) -> (batch, 1, enc_h)
        enc_out = encoder_outputs.permute(1, 2, 0)  # (seq_len, batch, enc_h) -> (batch, enc_h, seq_len)
        scores = torch.bmm(hW, enc_out)  # (batch, 1, seq_len)
        return scores.squeeze(1)


class ConcatAttention(AttentionScore):
    """
    """

    def __init__(self, hidden_size, encoder_hidden_size, decoder_hidden_size):
        super(ConcatAttention, self).__init__()
        self.W = nn.Linear(in_features=encoder_hidden_size + decoder_hidden_size, out_features=hidden_size, bias=False)
        self.v = nn.Linear(in_features=hidden_size, out_features=1, bias=False)

    def forward(self, hidden, encoder_outputs):
        seq_len = encoder_outputs.size(0)
        h = hidden.expand(seq_len, -1, -1)  # (batch, hidden_size) -> (seq_len, batch, hidden_size)
        scores = self.W(torch.cat([encoder_outputs, h], dim=2))  # (seq_len, batch, attn_h)
        scores = self.v(torch.tanh(scores))  # (seq_len, batch, 1)
        return scores.squeeze(2).transpose(0, 1)


# if __name__ == '__main__':
#     batch_size = 2
#     hidden_size = 10
#     encoder_hidden_size = 6
#     seq_len = 6
#     decoder_hidden_size = 5
#     t = 3
#
#     torch.manual_seed(287)
#
#     hidden = torch.randn(batch_size, decoder_hidden_size)
#     enc_out = torch.randn(seq_len, batch_size, encoder_hidden_size)
#
#     score = ConcatAttention(hidden_size, encoder_hidden_size, decoder_hidden_size)
#     # att = GeneralAttention(encoder_hidden_size, decoder_hidden_size)
#     # att = DotAttention()
#
#     # att = GlobalAttention(score)
#     att = LocalPredictiveAttention(score, hidden_size, decoder_hidden_size, 1)
#
#     attn_weights, context = att(t, hidden, enc_out)
#     print_dim('attn_weights', attn_weights)
#     print_dim('context', context)
