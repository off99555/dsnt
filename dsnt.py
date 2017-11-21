'''
A Tensorflow implementation of the DSNT layer, as taken from the paper "Numerical Coordinate
Regression with Convolutional Neural Networks"
'''

import tensorflow as tf

def dsnt(inputs, method='softmax'):
    '''
    Differentiable Spatial to Numerical Transform, as taken from the paper "Numerical Coordinate
    Regression with Convolutional Neural Networks"
    Arguments: 
        inputs - The learnt heatmap. A 3d tensor of shape [batch, height, width]
        method - A string representing the normalisation method. See `_normalise_heatmap` for available methods
    Returns:
        norm_heatmap - The given heatmap with normalisation/rectification applied
        coords_zipped - A tensor of shape [batch, 2] containing the [x, y] coordinate pairs
    '''
    # Rectify and reshape inputs
    norm_heatmap = _normalise_heatmap(inputs, method)
    
    batch_count = tf.shape(norm_heatmap)[0]
    height = tf.shape(norm_heatmap)[1]
    width = tf.shape(norm_heatmap)[2]

    # Build the DSNT x, y matrices
    dsnt_x = tf.tile([[(2 * tf.range(1, width+1) - (width + 1)) / width]], [batch_count, height, 1])
    dsnt_x = tf.cast(dsnt_x, tf.float32)
    dsnt_y = tf.tile([[(2 * tf.range(1, height+1) - (height + 1)) / height]], [batch_count, width, 1])
    dsnt_y = tf.cast(tf.transpose(dsnt_y, perm=[0, 2, 1]), tf.float32)

    # Compute the Frobenius inner product
    outputs_x = tf.reduce_sum(tf.multiply(norm_heatmap, dsnt_x), axis=[1, 2])
    outputs_y = tf.reduce_sum(tf.multiply(norm_heatmap, dsnt_y), axis=[1, 2])

    # Zip into [x, y] pairs
    coords_zipped = tf.stack([outputs_x, outputs_y], axis=1)

    return norm_heatmap, coords_zipped

def js_reg_loss(heatmaps, centres, fwhm=1):
    '''
    Calculates and returns the average Jensen-Shannon divergence between heatmaps and target Gaussians.
    Arguments:
        heatmaps - Heatmaps generated by the model
        centres - Centres of the target Gaussians (in normalized units)
        fwhm - Full-width-half-maximum for the drawn Gaussians, which can be thought of as a radius.
    '''
    gauss = _make_gaussians(centres, tf.shape(heatmaps)[1], tf.shape(heatmaps)[2], fwhm)
    divergences = _js_2d(heatmaps, gauss)
    return tf.reduce_mean(divergences)


def _normalise_heatmap(inputs, method='softmax'):
    '''
    Applies the chosen normalisation/rectification method to the input tensor
    Arguments:
        inputs - A 4d tensor of shape [batch, height, width, 1] (the learnt heatmap)
        method - A string representing the normalisation method. One of those shown below
    '''
    # Remove the final dimension as it's of size 1
    inputs = tf.reshape(inputs, tf.shape(inputs)[:3])

    # Normalise the values such that the values sum to one for each heatmap
    normalise = lambda x: tf.div(x, tf.reshape(tf.reduce_sum(x, [1, 2]), [2, 1, 1]))

    # Perform rectification
    if method == 'softmax':
        inputs = _softmax2d(inputs, axes=[1, 2])
    elif method == 'abs':
        inputs = tf.abs(inputs)
        inputs = normalise(inputs)
    elif method == 'relu':
        inputs = tf.nn.relu(inputs)
        inputs = normalise(inputs)
    elif method == 'sigmoid':
        inputs = tf.nn.sigmoid(inputs)
        inputs = normalise(inputs)
    else:
        msg = "Unknown rectification method \"{}\"".format(method)
        raise ValueError(msg)
    return inputs

def _kl_2d(p, q, eps=24):
    unsummed_kl = p * (tf.log(p + eps) - tf.log(q + eps))
    kl_values = tf.reduce_sum(unsummed_kl, [-1, -2])
    return kl_values

def _js_2d(p, q, eps=1e-24):
    m = 0.5 * (p + q)
    return 0.5 * _kl_2d(p, m, eps) + 0.5 * _kl_2d(q, m, eps)

def _softmax2d(target, axes):
    '''
    A softmax implementation which can operate across more than one axis - as this isn't
    provided by Tensorflow
    Arguments:
        targets - The tensor on which to apply softmax
        axes - An integer or list of integers across which to apply softmax
    '''
    max_axis = tf.reduce_max(target, axes, keep_dims=True)
    target_exp = tf.exp(target-max_axis)
    normalize = tf.reduce_sum(target_exp, axes, keep_dims=True)
    softmax = target_exp / normalize
    return softmax

def _make_gaussian(size, centre, fwhm=1):
        '''
        Makes a rectangular gaussian kernel.
        Arguments:
            size - A 2d tensor representing [height, width]
            centre - Pair of (normalised [0, 1]) x, y coordinates 
            fwhm - Full-width-half-maximum, which can be thought of as a radius.
        '''
        # Scale the normalised coordinates to be relative to the size of the frame
        centre = [centre[0] * tf.cast(size[1], tf.float32), 
                  centre[1] * tf.cast(size[0], tf.float32)]
        # Find the largest side, as we build a square and crop to desired size
        square_size = tf.cast(tf.reduce_max(size), tf.float32)

        x = tf.range(0, square_size, 1, dtype=tf.float32)
        y = x[:,tf.newaxis]
        x0 = centre[0] - 0.5
        y0 = centre[1] - 0.5
        unnorm = tf.exp(-4*tf.log(2.) * ((x-x0)**2 + (y-y0)**2) / fwhm**2)[:size[0],:size[1]]
        norm = unnorm / tf.reduce_sum(unnorm)
        return norm

def _make_gaussians(centres_in, height, width, fwhm=1):
    '''
    Makes a batch of gaussians. Size of images designated by height, width; number of images
    designated by length of the 1st dimension of centres_in
    Arguments:
        centres_in - The normalised coordinate centres of the gaussians of shape [batch, x, y]
        height - The desired height of the produced gaussian image
        width - The desired width of the produced gaussian image
        fwhm - Full-width-half-maximum, which can be thought of as a radius.
    '''
    def cond(centres, heatmaps):
        return tf.greater(tf.shape(centres)[0], 0)
    
    def body(centres, heatmaps):
        curr = centres[0]
        centres = centres[1:]
        new_heatmap = _make_gaussian([height, width], curr, fwhm)
        new_heatmap = tf.reshape(new_heatmap, [-1])
        
        heatmaps = tf.concat([heatmaps, new_heatmap], 0)
        return [centres, heatmaps]
    
    # Produce 1 heatmap per coordinate pair, build a list of heatmaps
    _, heatmaps_out = tf.while_loop(cond,
                                    body,
                                    [centres_in, tf.constant([])],
                                    shape_invariants=[tf.TensorShape([None, 2]), tf.TensorShape([None])])
    heatmaps_out = tf.reshape(heatmaps_out, [-1, height, width])
    return heatmaps_out