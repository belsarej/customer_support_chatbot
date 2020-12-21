#!/usr/bin/env python3

import torch
import torch.nn as nn
import os
import argparse
from model import predict_model_factory
from dataset import field_factory, metadata_factory
from serialization import load_object
from constants import MODEL_START_FORMAT
from flask import Flask, render_template, request
app = Flask(__name__)
app.static_folder = 'static'



class ModelDecorator(nn.Module):
      def __init__(self, model):
        super(ModelDecorator, self).__init__()
        self.model = model

      def forward(self, question, sampling_strategy, max_seq_len):
        return self.model([question], sampling_strategy, max_seq_len)[0]


customer_service_models = {
    'amazon': ('trained-models/amazon', 10),
}

model_path = 'trained-models/amazon'
epoch = 10

def get_model_path(dir_path, epoch):
    name_start = MODEL_START_FORMAT % epoch
    for path in os.listdir(dir_path):
        if path.startswith(name_start):
            return dir_path + path
    raise ValueError("Model from epoch %d doesn't exist in %s" % (epoch, dir_path))



torch.set_grad_enabled(False)
   # args = parse_args()
   # print('Args loaded')
 #   model_args = load_object(args.model_path + os.path.sep + 'args')
model_args = load_object(model_path + os.path.sep + 'args')
   # print('Model args loaded.')
vocab = load_object(model_path + os.path.sep + 'vocab')
   # print('Vocab loaded.')

#cuda = torch.cuda.is_available() and args.cuda
#torch.set_default_tensor_type(torch.cuda.FloatTensor if cuda else torch.FloatTensor)
   # print("Using %s for inference" % ('GPU' if cuda else 'CPU'))

field = field_factory(model_args)
field.vocab = vocab
metadata = metadata_factory(model_args, vocab)

model = ModelDecorator(
   predict_model_factory(model_args, metadata, get_model_path(model_path + os.path.sep, epoch), field))
   # print('model loaded')

model.eval()

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/get")
def get_bot_response():
    userText = request.args.get('msg')
    response = model(userText, sampling_strategy='greedy', max_seq_len=50)
    return str((response))

if __name__ == "__main__":
    app.run(debug=False,host='0.0.0.0',port=5000)
    

     
