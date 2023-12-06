import torch
from torch import nn
from torch.utils.data import DataLoader
import torchmetrics
from typing import Optional
import numpy as np
import pandas as pd
from datasets.preprocess import preprosess_Module
from nn.model import ANN
from nn.utils import CustomDataset
from tqdm.auto import tqdm
import argparse
from nn.validation import *
from tqdm.auto import tqdm
from torch.utils.data import TensorDataset 
from nn.early_stop import EarlyStopper
from nn.rmsle import RMSLELoss, RMSELoss
from datasets.dataset import get_X, get_y
from metric.graph import get_graph
from torch.optim.lr_scheduler import ReduceLROnPlateau

device = 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
#print(device)

def train(
  model:nn.Module,
  criterion:callable,
  optimizer:torch.optim.Optimizer,
  data_loader:DataLoader,
  device:str
) -> float:
  '''train one epoch
  
  Args:
      model: model
      criterion: loss
      optimizer: optimizer
      data_loader: data loader
      device: device
  '''
  model.train()
  total_loss = 0.
  for X, y in data_loader:
    X, y = X.to(device), y.to(device)
    output = model(X)
    loss = criterion(output, y)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
    total_loss += loss.item() * len(y)
  return total_loss/len(data_loader.dataset)

def evaluate(
  model:nn.Module,
  criterion:callable,
  data_loader:DataLoader,
  device:str,
  metric:Optional[torchmetrics.metric.Metric]=None,
) -> float:
  '''evaluate
  
  Args:
      model: model
      criterions: list of criterion functions
      data_loader: data loader
      device: device
  '''
  model.eval()
  total_loss,correct = 0.,0.
  with torch.inference_mode():
    for X, y in data_loader:
      X, y = X.to(device), y.to(device)
      output = model(X)
      total_loss += criterion(output, y).item() * len(y)
      #correct = (output > 0.5).astype(np.float32)
      #correct += (output.argmax(1) == y).type(torch.float).sum().item()
      if metric is not None:
        #print(output.squeeze(1).squeeze(),y.squeeze(1).squeeze())
        output = torch.round(output)
        metric.update_state(output, y)
  #acc = correct / len(data_loader.dataset)
  total_loss = total_loss/len(data_loader.dataset)
  return total_loss 


def main(args):
  train_params = args.get("train_params")
  files_ = args.get("files")
  device = torch.device(train_params.get("device"))
  model_params = args.get("model_params")

  # device = torch.device(args.device)

  # submission_df = pd.read_csv(args.data_submission)
  submission_df = pd.read_csv(files_.get("data_submission"))


  train_df = pd.read_csv(files_.get("data_train"))
  test_df = pd.read_csv(files_.get("data_test"))
  X_trn, X_val = get_X(train_df,test_df)
  y_trn = get_y(train_df,test_df)
  #y_trn = get_y(train_df,test_df)[:,np.newaxis]
  ds = CustomDataset(X_trn.astype(np.float32), y_trn.astype(np.float32))
  ds_val = CustomDataset(X_val.astype(np.float32))
  # dl = DataLoader(ds, batch_size=args.batch_size, shuffle=args.shuffle)

  dl_params = train_params.get("data_loader_params")
  dl = DataLoader(ds, batch_size=dl_params.get("batch_size"), shuffle=dl_params.get("shuffle"))
  dl_val = DataLoader(ds_val, batch_size=dl_params.get("batch_size"))

  model = ANN(X_trn.shape[-1] ,model_params.get("hidden_dim")).to(device)
  print(model)
  opt_params = train_params.get("optim_params")
  optimizer = torch.optim.AdamW(model.parameters(), lr=opt_params.get("lr"))
  scheduler = ReduceLROnPlateau(optimizer,'min',factor=0.7,patience=3,min_lr=0.000001)

  history = {
    'loss':[],
    'val_loss':[],
    'lr':[]
  }
  
  if args.get("train"):
    pbar = range(train_params.get("epochs"))
    if train_params.get("pbar"):
      pbar = tqdm(pbar)
    
    print("Learning Start!")
    early_stopper = EarlyStopper(train_params.get("patience") ,train_params.get("min_delta"))
    for _ in pbar:
      loss = train(model, RMSLELoss(), optimizer, dl, device)
      history['lr'].append(optimizer.param_groups[0]['lr'])
      scheduler.step(loss)
      history['loss'].append(loss)
      pbar.set_postfix(trn_loss=loss)
      if early_stopper.early_stop(model, loss, files_.get("output")+files_.get("name")+'_earlystop.pth'):
        print('Early Stopper run!')            
        break
    get_graph(history, files_.get("name"))
    #evaluate(model, nn.functional.binary_cross_entropy, dl, device)    
    print("Done!")
    torch.save(model.state_dict(), files_.get("output")+files_.get("name")+'.pth')
    
    model = ANN(X_trn.shape[-1] ,model_params.get("hidden_dim")).to(device)
    if torch.load(files_.get("output")+files_.get("name")+'_earlystop.pth'):
      model.load_state_dict(torch.load(files_.get("output")+files_.get("name")+'_earlystop.pth'))
    else:
      model.load_state_dict(torch.load(files_.get("output")+files_.get("name")+'.pth'))
    model.eval()
    weights = np.array([10,5,3,1])
    pred = []
    with torch.inference_mode():
      for x in dl_val:
        x = x[0].to(device)
        out = model(x)
        pred.append(out.detach().cpu().numpy())
      pred = np.dot(np.concatenate(pred),weights)
    
    submission_df['ECLO'] = pred.squeeze()
    submission_df.to_csv(files_.get("submission")+files_.get("name")+'.csv',index=False)
  
  print('------------------------------------------------------------------')
  if args.get("validation"):
    model = ANN(X_trn.shape[-1] ,model_params.get("hidden_dim")).to(device)
    scores = Validation(X_trn, y_trn, train_params.get("patience"), train_params.get("min_delta"))
    scores = pd.DataFrame(scores.kfold(model, n_splits=5, epochs=train_params.get("epochs"), lr=opt_params.get("lr"), 
                                       batch=dl_params.get("batch_size"), shuffle=True, random_state=2023))
    print(pd.concat([scores, scores.apply(['mean', 'std'])]))
    
  return


# def get_args_parser(add_help=True):

#   parser = argparse.ArgumentParser(description="PyTorch Classification Training", add_help=add_help)

#   parser.add_argument("--data-submission", default="/home/estsoft/data/sample_submission.csv", type=str, help="submission dataset path") #files
#   parser.add_argument("--data-train", default="/home/estsoft/data/train.csv", type=str, help="train dataset path") # files
#   parser.add_argument("--data-test", default="/home/estsoft/data/test.csv", type=str, help="test dataset path") #files
#   parser.add_argument("--hidden-dim", default=64, type=int, help="dimension of hidden layer") # layer 자체를 바꾸기
#   parser.add_argument("--device", default="cuda", type=str, help="device (Use cpu/cuda/mps)") #train_params
#   parser.add_argument("-b", "--batch-size", default=64, type=int, help="batch size") #train_params - data_loader_params
#   parser.add_argument("--shuffle", default=True, type=bool, help="shuffle") #train_params - data_loader_params
#   parser.add_argument("--epochs", default=100, type=int, metavar="N", help="number of total epochs to run")
#   parser.add_argument("--lr", default=0.001, type=float, help="learning rate") #train_params - optim_params
#   parser.add_argument("--pbar", default=True, type=bool, help="progress bar") #train_params
#   parser.add_argument("-o", "--output", default="./submit/model_", type=str, help="path to save output model") #files
#   parser.add_argument("-sub", "--submission", default="./submit/submission_", type=str, help="path to save submission")
#   parser.add_argument("-train", "--train", default=False, type=bool, help="full data set train")
#   parser.add_argument("-val", "--validation", default=False, type=bool, help="kfold cross validation train")
#   parser.add_argument("-pat", "--patience", default=5, type=int, help="Early stop patience count")
#   parser.add_argument("-delta", "--min-delta", default=0, type=int, help="Early stop delta value")
#   parser.add_argument("-name", "--name", default="", type=str, help="model name for Outputs")
  
  
#   return parser


def get_args_parser(add_help=True):
    import argparse

    parser = argparse.ArgumentParser(description="Pytorch K-fold Cross Validation", add_help=add_help)
    parser.add_argument(
        "-c", "--config", default="./config.py", type=str, help="configuration file"
    )

    return parser




if __name__ == "__main__":
  # args = get_args_parser().parse_args()
  # main(args)

  args = get_args_parser().parse_args()
  print('args start on train')
  print(args)
  print('args end on train')

  exec(open(args.config).read())
  main(config)
