"""ResNet model implemented using slim components

Related ResNet papers:
https://arxiv.org/pdf/1603.05027v2.pdf
https://arxiv.org/pdf/1512.03385v1.pdf
https://arxiv.org/pdf/1605.07146v1.pdf
"""
from collections import namedtuple
from libs import custom_ops

import numpy as np
import tensorflow as tf
import tensorflow.contrib.slim as slim


HParams = namedtuple('HParams',
                     'batch_size, num_classes, min_lrn_rate, lrn_rate, '
                     'num_residual_units, use_bottleneck, weight_decay_rate, '
                     'relu_leakiness, optimizer')


class ResNet(object):
  """ResNet model."""

  def __init__(self, hps, images, labels, mode):
    """ResNet constructor.

    Args:
      hps: Hyperparameters.
      images: Batches of images. [batch_size, image_size, image_size, 3]
      labels: Batches of labels. [batch_size, num_classes]
      mode: One of 'train' and 'eval'.
    """
    self.hps = hps
    self._images = images
    self.labels = labels
    self.mode = mode

    self._extra_train_ops = []

  def build_graph(self):
    """Build a whole graph for the model."""
    self.global_step = tf.Variable(0, name='global_step', trainable=False)
    self._build_model()
    custom_ops.log_number_of_params()
    if self.mode == 'train':
      self._build_train_op()
    self.summaries = tf.merge_all_summaries()

  def _build_model(self):
    """Build the core model within the graph."""
    x = self._images
    tf.logging.info('Image Shape: %s', x.get_shape())

    with tf.variable_scope('init'):
      x = self._conv('init_conv', x, 16, stride=1)

      tf.logging.info('Initial Output: %s', x.get_shape())

    with tf.variable_scope('block1-split'):
      x_A, x_B = tf.split(3, 2, x)

    with tf.variable_scope('block1'):
      tf.logging.info("Block 1, input: %s", x.get_shape())
      x_A = self.stage(x_A, self.hps.num_residual_units, 8, first_layer_stride=1, scope='A')
      x_B = self.stage(x_B, self.hps.num_residual_units, 8, first_layer_stride=1, scope='B')

    with tf.variable_scope('stage2-split'):
      x_AA, x_AB = tf.split(3, 2, x_A)
      x_BA, x_BB = tf.split(3, 2, x_B)

    with tf.variable_scope('block2'):
      tf.logging.info("Block 2, input: %s", x_A.get_shape())
      x_AA = self.stage(x_AA, self.hps.num_residual_units, 8, first_layer_stride=2, scope='AA')
      x_AB = self.stage(x_AB, self.hps.num_residual_units, 8, first_layer_stride=2, scope='AB')
      x_BA = self.stage(x_BA, self.hps.num_residual_units, 8, first_layer_stride=2, scope='BA')
      x_BB = self.stage(x_BB, self.hps.num_residual_units, 8, first_layer_stride=2, scope='BB')

    with tf.variable_scope('stage2-split'):
      x_AAA, x_AAB = tf.split(3, 2, x_AA)
      x_ABA, x_ABB = tf.split(3, 2, x_AB)
      x_BAA, x_BAB = tf.split(3, 2, x_BA)
      x_BBA, x_BBB = tf.split(3, 2, x_BB)

    with tf.variable_scope('block3'):
      tf.logging.info("Block 3, input: %s", x_AA.get_shape())
      x_AAA = self.stage(x_AAA, self.hps.num_residual_units, 8, first_layer_stride=2, scope='AAA')
      x_AAB = self.stage(x_AAB, self.hps.num_residual_units, 8, first_layer_stride=2, scope='AAB')
      x_ABA = self.stage(x_ABA, self.hps.num_residual_units, 8, first_layer_stride=2, scope='ABA')
      x_ABB = self.stage(x_ABB, self.hps.num_residual_units, 8, first_layer_stride=2, scope='ABB')
      x_BAA = self.stage(x_BAA, self.hps.num_residual_units, 8, first_layer_stride=2, scope='BAA')
      x_BAB = self.stage(x_BAB, self.hps.num_residual_units, 8, first_layer_stride=2, scope='BAB')
      x_BBA = self.stage(x_BBA, self.hps.num_residual_units, 8, first_layer_stride=2, scope='BBA')
      x_BBB = self.stage(x_BBB, self.hps.num_residual_units, 8, first_layer_stride=2, scope='BBB')

    with tf.variable_scope('unify'):
      x = tf.concat(3, [x_AAA, x_AAB, x_ABA, x_ABB, x_BAA, x_BAB, x_BBA, x_BBB])
      tf.logging.info('Concat Output: %s', x.get_shape())

    with tf.variable_scope('final'):
      x = self._conv('unify', x, 40, 1)
      x = self._batch_norm(x)
      x = self._relu(x, self.hps.relu_leakiness)
      # avg pool
      x = self._global_avg_pool(x)
      tf.logging.info('Final Layer Output: %s', x.get_shape())

    with tf.variable_scope('logit'):
      x = slim.layers.flatten(x)
      tf.logging.info('Flatten Output: %s', x.get_shape())
      self.logits = self._fully_connected(x, self.hps.num_classes)
      self.predictions = tf.nn.softmax(self.logits)

    with tf.variable_scope('costs'):
      xent = tf.nn.softmax_cross_entropy_with_logits(
          self.logits, self.labels)
      self.cost = tf.reduce_mean(xent, name='xent')
      self.cost += self._decay()

      tf.scalar_summary(self.mode + '/cost', self.cost)

  def stage(self, x, n_residuals, out_filter, first_layer_stride=2, scope='default_stage'):
    with tf.variable_scope(scope):
      tf.logging.info("Stage " + scope)
      with tf.variable_scope('residual_' + str(0)):
        x = self._residual(x, out_filter, stride=first_layer_stride)
        #x = self._highway(x, out_filter, bias_init=-2, stride=first_layer_stride)
      for i in range(1, n_residuals):
        with tf.variable_scope('residual_' + str(i)):
          x = self._residual(x, out_filter, stride=1)
          #x = self._highway(x, out_filter, bias_init=-2, stride=1)
      return x

  def _classic(self, x, out_filter, stride=1):
    x = self._batch_norm(x)
    x = self._relu(x, self.hps.relu_leakiness)
    x = self._conv('conv', x, out_filter, stride=stride)
    tf.logging.info('Classic Block Output: %s', x.get_shape())
    return x

  def _residual(self, x, out_filter, stride=1):
    """Residual unit with 2 sub layers."""
    orig_x = x

    with tf.variable_scope('sub1'):
      x = self._batch_norm(x)
      x = self._relu(x, self.hps.relu_leakiness)
      x = self._conv('conv1', x, out_filter, stride=stride)

    with tf.variable_scope('sub2'):
      x = self._batch_norm(x)
      x = self._relu(x, self.hps.relu_leakiness)
      x = self._conv('conv2', x, out_filter, stride=1)

    with tf.variable_scope('sub_add'):
      in_filter = orig_x.get_shape()[-1].value
      in_kernel_size = orig_x.get_shape()[-2].value
      out_kernel_size = x.get_shape()[-2].value
      if in_filter != out_filter or in_kernel_size != out_kernel_size:
        orig_x = tf.nn.avg_pool(orig_x, [1, stride, stride, 1], [1, stride, stride, 1], 'VALID')
        orig_x = tf.pad(
            orig_x, [[0, 0], [0, 0], [0, 0],
                     [(out_filter-in_filter)//2, (out_filter-in_filter)//2]])
        tf.logging.info("avg pooling to fit dimensions. Add out: %s", x.get_shape())
      x += orig_x

    tf.logging.info('Residual Block Output: %s', x.get_shape())
    return x

  def _highway(self, x, out_filter, bias_init, stride=1):
    """Highway unit with 2 sub layers."""
    orig_x = x

    with tf.variable_scope('sub1'):
      x = self._batch_norm(x)
      x = self._relu(x, self.hps.relu_leakiness)
      x = self._conv('conv1', x, out_filter, stride=stride)

    with tf.variable_scope('sub2'):
      x = self._batch_norm(x)
      x = self._relu(x, self.hps.relu_leakiness)
      x = self._conv('conv2', x, out_filter, stride=1)

    with tf.variable_scope('sub_add'):
      in_filter = orig_x.get_shape()[-1].value
      if in_filter != out_filter:
        orig_x = tf.nn.avg_pool(orig_x, [1, stride, stride, 1], [1, stride, stride, 1], 'VALID')
        orig_x = tf.pad(orig_x, [[0, 0], [0, 0], [0, 0],
                     [(out_filter-in_filter)//2, (out_filter-in_filter)//2]])
        tf.logging.info("avg pooling to fit dimensions. Add out: %s", x.get_shape())

      filter_size = 3
      n = filter_size * filter_size * out_filter
      T = slim.conv2d(x, out_filter, [3, 3], stride=1,
                      weights_initializer=tf.random_normal_initializer(stddev=np.sqrt(2.0/n)),
                      biases_initializer=tf.constant_initializer(bias_init),
                      activation_fn=tf.nn.sigmoid,
                      scope='transform_gate_1')

      # bias_init leads the network initially to be biased towards carry behaviour (i.e. T = 0)
      x = T * x  +  (1.0 - T) * orig_x

    tf.logging.info('Highway Block Output: %s', x.get_shape())
    return x

  def _build_train_op(self):
    """Build training specific ops for the graph."""
    self.lrn_rate = tf.constant(self.hps.lrn_rate, tf.float32)
    tf.scalar_summary(self.mode + '/learning rate', self.lrn_rate)

    trainable_variables = tf.trainable_variables()
    grads = tf.gradients(self.cost, trainable_variables)

    if self.hps.optimizer == 'sgd':
      optimizer = tf.train.GradientDescentOptimizer(self.lrn_rate)
    elif self.hps.optimizer == 'mom':
      #optimizer = tf.train.AdamOptimizer(0.001)
      #ooptimizer = tf.train.MomentumOptimizer(self.lrn_rate, 0.9, use_nesterov=True)
      optimizer = tf.train.MomentumOptimizer(self.lrn_rate, 0.9)

    apply_op = optimizer.apply_gradients(
        zip(grads, trainable_variables),
        global_step=self.global_step, name='train_step')

    train_ops = [apply_op] + self._extra_train_ops + tf.get_collection(tf.GraphKeys.UPDATE_OPS)
    self.train_op = tf.group(*train_ops)

  def _decay(self):
    """L2 weight decay loss."""
    costs = []
    for var in tf.trainable_variables():
      if var.op.name.find(r'weights') > 0:
        costs.append(tf.nn.l2_loss(var))
        #tf.histogram_summary(self.mode + '/' + var.op.name, var)

    return tf.mul(self.hps.weight_decay_rate, tf.add_n(costs))

  def _batch_norm(self, x):
    if self.mode == 'train':
      return slim.layers.batch_norm(x, scale=False, decay=0.9, scope='bn_2', is_training=True)
    else:
      return slim.layers.batch_norm(x, scale=False, decay=0.9, scope='bn_2', is_training=False)

  def _relu(self, x, leakiness=0.0):
    """Relu, with optional leaky support."""
    return tf.select(tf.less(x, 0.0), leakiness * x, x, name='leaky_relu')

  def _conv(self, name, x, out_filters, stride):
    filter_size = 3
    n = filter_size * filter_size * out_filters
    return slim.layers.conv2d(x, out_filters, [filter_size, filter_size], stride=stride,
                              padding='SAME', activation_fn=None,
                              weights_initializer=tf.random_normal_initializer(stddev=np.sqrt(2.0/n)),
                              #weights_initializer=tf.random_normal_initializer(stddev=0.01),
                              #weights_initializer=tf.contrib.layers.variance_scaling_initializer(),
                              scope=name)

  def _fully_connected(self, x, out_dim):
    return slim.layers.fully_connected(x, out_dim,
                                       activation_fn=None,
                                       #weights_initializer=tf.uniform_unit_scaling_initializer(factor=1.0)
                                       weights_initializer=tf.uniform_unit_scaling_initializer(factor=1.0)
                                       #weights_initializer=tf.random_normal_initializer(stddev=0.01)
                                       #weights_initializer=tf.contrib.layers.variance_scaling_initializer()
                                       )

  def _max_pool(self, x):
    x = tf.nn.max_pool(x, ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding="SAME")
    tf.logging.info('Max-Pool Output: %s', x.get_shape())
    return x

  def _global_avg_pool(self, x):
    assert x.get_shape().ndims == 4
    return tf.reduce_mean(x, [1, 2])

