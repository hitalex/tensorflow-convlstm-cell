import tensorflow as tf
from tensorflow.python.ops.array_ops import concat, reshape, split
from tensorflow.python.ops.init_ops import zeros_initializer, constant_initializer
from tensorflow.python.ops.math_ops import sigmoid, tanh
from tensorflow.python.ops.nn_ops import convolution
from tensorflow.python.ops.rnn_cell import LSTMStateTuple, RNNCell
from tensorflow.python.ops.variable_scope import get_variable, variable_scope


class ConvLSTMCell(RNNCell):
  """A LSTM cell with convolutions instead of multiplications.

  Reference:
    Xingjian, S. H. I., et al. "Convolutional LSTM network: A machine learning approach for precipitation nowcasting." Advances in Neural Information Processing Systems. 2015.
  """

  def __init__(self, height, width, filters, is_training=tf.placeholder(tf.bool), kernel=[3, 3], normalize_timesteps=10, initializer=tf.orthogonal_initializer(), forget_bias=1.0, activation=tf.tanh):
    self._height = height
    self._width = width
    self._filters = filters
    self._kernel = kernel
    self._is_training = is_training
    self._initializer = initializer
    self._forget_bias = forget_bias
    self._activation = activation
    self._normalize_timesteps = normalize_timesteps
    self._normalize = normalize_timesteps > 0

  @property
  def state_size(self):
    size = self._height * self._width * self._filters
    return LSTMStateTuple(size, size)

  @property
  def output_size(self):
    return self._height * self._width * self._filters

  def __call__(self, input, state, scope=None):
    with variable_scope(scope or self.__class__.__name__):
      previous_memory, previous_output = state

      with variable_scope('Expand'):
        samples = input.get_shape()[0].value
        shape = [samples, self._height, self._width]
        input = reshape(input, shape + [-1])
        previous_memory = reshape(previous_memory, shape + [self._filters])
        previous_output = reshape(previous_output, shape + [self._filters])

      with variable_scope('Convolve'):
        channels = input.get_shape()[-1].value
        filters = self._filters
        gates = 4 * filters if filters > 1 else 4

        # The input-to-hidden and hidden-to-hidden weights can be summed directly if batch normalization is not needed.
        if not self._normalize:
          x = concat(3, [input, previous_output])  # TODO Update to TensorFlow 1.0.
          n = channels + filters
          m = gates
          W = get_variable('Weights', self._kernel + [n, m], initializer=self._initializer)
          y = convolution(x, W, 'SAME')
        else:
          with variable_scope('Input'):
            x = input
            n = channels
            m = gates
            W = get_variable('Weights', self._kernel + [n, m], initializer=self._initializer)
            Wxh = convolution(x, W, 'SAME')
            Wxh = self._batch_norm(Wxh)

          with variable_scope('Hidden'):
            x = previous_output
            n = filters
            m = gates
            W = get_variable('Weights', self._kernel + [n, m], initializer=self._initializer)
            Whh = convolution(x, W, 'SAME')
            Whh = self._batch_norm(Whh)

          y = Wxh + Whh

        y += get_variable('Biases', [m], initializer=zeros_initializer)

        input, input_gate, forget_gate, output_gate = split(3, 4, y)  # TODO Update to TensorFlow 1.0.

      with variable_scope('LSTM'):
        memory = (previous_memory
          * sigmoid(forget_gate + self._forget_bias)
          + sigmoid(input_gate) * self._activation(input))
        if self._normalize:
          memory = self._batch_norm(memory)
        output = self._activation(memory) * sigmoid(output_gate)

      with variable_scope('Flatten'):
        shape = [-1, self._height * self._width * self._filters]
        output = reshape(output, shape)
        memory = reshape(memory, shape)

      return output, LSTMStateTuple(memory, output)

  def _batch_norm(self, tensor):
      """Batch normalization for individual RNN timesteps.

      Initial gammas should be around 0.1 according to Cooijmans, Tim, et al. "Recurrent Batch Normalization." arXiv preprint arXiv:1603.09025 (2016).
      """
      from tensorflow.contrib.layers import batch_norm
      step = tf.train.get_global_step()
      timestep = tf.cond(self._is_training,
                         lambda: tf.mod(step, self._normalize_timesteps),
                         lambda: step)
      batch_norms = [lambda: batch_norm(tensor,
                     scale=True,
                     is_training=self._is_training,
                     updates_collections=None,
                     param_initializers={'gamma': constant_initializer(0.1)})
                     for _ in range(self._normalize_timesteps)]
      predicates = [tf.equal(timestep, x)
                    for x in range(self._normalize_timesteps)]
      x = batch_norms[-1]()
      for i in range(self._normalize_timesteps):
        x = tf.cond(predicates[i], lambda: batch_norms[i](), lambda: x)
      return x
      """TODO Use tf.case instead when fixed: http://stackoverflow.com/questions/40910834/how-to-duplicate-input-tensors-conditional-on-a-tensor-attribute-oversampling
      return tf.case(list(zip(predicates, batch_norms)),
                     default=batch_norms[-1],
                     exclusive=True)
      """


def flatten(tensor):
  samples, timesteps, height, width, filters = tensor.get_shape().as_list()
  return reshape(tensor, [samples, timesteps, height * width * filters])


def expand(tensor, height, width):
  samples, timesteps, features = tensor.get_shape().as_list()
  return reshape(tensor, [samples, timesteps, height, width, -1])
