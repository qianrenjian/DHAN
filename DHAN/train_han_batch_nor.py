import os
import time
import pickle
import random
import numpy as np
import tensorflow as tf
import sys
from input import DataInput, DataInputTest
from model_han_batch_nor import Model
import argparse
from tensorflow.python.lib.io import file_io
import threading

os.environ['CUDA_VISIBLE_DEVICES'] = '0'
random.seed(1234)
np.random.seed(1234)
tf.set_random_seed(1234)

train_batch_size = 32
test_batch_size = 512
predict_batch_size = 32
predict_users_num = 1000
predict_ads_num = 100

parser = argparse.ArgumentParser()
tf.app.flags.DEFINE_string("buckets", "", "buckets info")
tf.app.flags.DEFINE_string("checkpointDir", "", "oss info")
tf.app.flags.DEFINE_string("mode","train","mode selection")
tf.app.flags.DEFINE_string("testOutputDir","./","test output folder")
#tf.app.flags.DEFINE_string("logdir","","tensorboard info")
FLAGS = tf.app.flags.FLAGS
print(FLAGS.buckets)
dataset_file=os.path.join(FLAGS.buckets,'dataset.pkl')
print('dataset_file is {}'.format(dataset_file))


with file_io.FileIO(dataset_file, 'rb') as f:
  train_set = pickle.load(f)
  print("train_set",train_set[1],len(train_set))
  print("train_set shape",type(train_set))
  test_set = pickle.load(f)
  print("test_set",test_set[10],len(test_set))
  print("test_set shape",type(test_set))
  cate_list = pickle.load(f)
  print("cate_list",cate_list[1],cate_list.shape)
  print("cate_list shape",type(cate_list))
  user_count, item_count, cate_count = pickle.load(f)
  print("user_count",user_count,"item_count",item_count,"cate_count",cate_count)

best_auc = 0.0
def calc_auc(raw_arr):
    """Summary

    Args:
        raw_arr (TYPE): Description

    Returns:
        TYPE: Description
    """
    # sort by pred value, from small to big
    arr = sorted(raw_arr, key=lambda d:d[2])

    auc = 0.0
    fp1, tp1, fp2, tp2 = 0.0, 0.0, 0.0, 0.0
    for record in arr:
        fp2 += record[0] # noclick
        tp2 += record[1] # click
        auc += (fp2 - fp1) * (tp2 + tp1)
        fp1, tp1 = fp2, tp2

    # if all nonclick or click, disgard
    threshold = len(arr) - 1e-3
    if tp2 > threshold or fp2 > threshold:
        return -0.5

    if tp2 * fp2 > 0.0:  # normal auc
        return (1.0 - auc / (2.0 * tp2 * fp2))
    else:
        return None

def _auc_arr(score):
  score_p = score[:,0]
  score_n = score[:,1]
  #print "============== p ============="
  #print score_p
  #print "============== n ============="
  #print score_n
  score_arr = []
  for s in score_p.tolist():
    score_arr.append([0, 1, s])
  for s in score_n.tolist():
    score_arr.append([1, 0, s])
  return score_arr
def _eval(sess, model):
  auc_sum = 0.0
  score_arr = []
  ct=0
  loss_sum = 0.0
  for _, uij in DataInputTest(test_set, test_batch_size):
    auc_, score_,loss = model.eval(sess, uij)
    ct = ct+1
    score_arr += _auc_arr(score_)
    auc_sum += auc_ * len(uij[0])
    loss_sum +=loss
  test_gauc = auc_sum / len(test_set)
  loss_sum = loss_sum /ct
  Auc = calc_auc(score_arr)
  global best_auc
  if best_auc < test_gauc:
    best_auc = test_gauc
    model.save(sess, FLAGS.checkpointDir)
  return test_gauc, Auc,loss_sum

def _test(sess, model):
  auc_sum = 0.0
  score_arr = []
  wt_cat_i_arr = []
  wt_cat_j_arr = []
  wt_i_arr = []
  wt_j_arr = []
  predicted_users_num = 0
  print ("test sub items")
  total_data = []
  for _, uij in DataInputTest(test_set, predict_batch_size):
    if predicted_users_num >= predict_users_num:
        break
    score_,score_negative,wt_cat_i,wt_cat_j,wt_i,wt_j,hist_i,hist_cat,item_i,item_j = model.test(sess, uij)
    score_arr.append(score_)
    for data in zip(score_,score_negative,wt_cat_i,wt_cat_j,wt_i,wt_j,hist_i,hist_cat,item_i,item_j):
      total_data.append(data)
    wt_cat_i_arr.append(wt_cat_i)
    wt_cat_j_arr.append(wt_cat_j)
    wt_i_arr.append(wt_i)
    wt_j_arr.append(wt_j)
    predicted_users_num += predict_batch_size
    #print('item_weight is {}, category weight is {}'.format(wt_i,wt_cat_i))
  with open(FLAGS.testOutputDir+'test_output.pkl','wb') as f:
    pickle.dump(total_data,f,pickle.HIGHEST_PROTOCOL)
  item_emb,cat_emb = model.get_embedding(sess)
  with open(FLAGS.testOutputDir+'embedding.pkl','wb') as f:
    pickle.dump((item_emb,cat_emb),f,pickle.HIGHEST_PROTOCOL)
  # with open(FLAGS.testOutputDir + 'item_embedding.pkl','wb') as f:
  #   pickle.dump(item_emb,pickle.HIGHEST_PROTOCOL)
  # with open(FLAGS.testOutputDir + 'cat_embedding.pkl','wb') as f:
  #   pickle.dump(cat_emb,pickle.HIGHEST_PROTOCOL)
  return score_[0]

gpu_options = tf.GPUOptions(allow_growth=True)
if FLAGS.mode == "train":
  with tf.Session(config=tf.ConfigProto(gpu_options=gpu_options)) as sess:

    model = Model(user_count, item_count, cate_count, cate_list, predict_batch_size, predict_ads_num)
    sess.run(tf.global_variables_initializer())
    sess.run(tf.local_variables_initializer())

    # print('test_gauc: %.4f\t test_auc: %.4f' % _eval(sess, model))
    sys.stdout.flush()
    lr = 1.0
    start_time = time.time()
    for _ in range(100):

      random.shuffle(train_set)

      epoch_size = round(len(train_set) / train_batch_size)
      loss_sum = 0.0
      for _, uij in DataInput(train_set, train_batch_size):
        loss = model.train(sess, uij, lr)
        loss_sum += loss
        # print('Epoch %d Global_step %d' % (model.global_epoch_step.eval(), model.global_step.eval()))

        if model.global_step.eval() % 1000 == 0:
          test_gauc, Auc,test_loss_sum = _eval(sess, model)
          print('BATCH_NOR_Epoch %d Global_step %d\tTrain_loss: %.4f\tEval_GAUC: %.4f\tEval_AUC: %.4f\tTest_loss: %.4f' %
                (model.global_epoch_step.eval(), model.global_step.eval(),
                loss_sum / 1000, test_gauc, Auc,test_loss_sum))
          print("cost",time.time()-start_time,"best_auc",best_auc)
          sys.stdout.flush()
          loss_sum = 0.0

      


        if model.global_step.eval() % 360000 == 0:
          lr = 0.1

      print('Epoch %d DONE\tCost time: %.2f' %
            (model.global_epoch_step.eval(), time.time()-start_time))
      sys.stdout.flush()
      model.global_epoch_step_op.eval()

    print('best test_gauc:', best_auc)
    sys.stdout.flush()
elif FLAGS.mode == "test":
  with tf.Session(config=tf.ConfigProto(gpu_options=gpu_options)) as sess:

    model = Model(user_count, item_count, cate_count, cate_list, predict_batch_size, predict_ads_num)
    model.restore(sess,FLAGS.checkpointDir)
    print("model load finish")

   
    score = _test(sess, model)
    print(score)
    sys.stdout.flush()
else:
  print("do nothing..")

