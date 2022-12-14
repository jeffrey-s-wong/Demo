print('Importing dependencies...')
import os
base_path = os.getcwd()
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '0'

import numpy as np
import tensorflow as tf
from transformers import BertTokenizer
import sentencepiece as spm
import opencc
from tokenizers.normalizers import BertNormalizer
print('Dependencies loaded')
print('Loading transformer model...')
# Hyperparameters
num_layers = 4
d_model = 128
dff = 512
num_heads = 8
dropout_rate = 0.1

class Normaliser():
    def __init__(self, text):
        self.text = text
        self.newstring = ""
        # Use BertNormalizer to convert clean
        self.normalizer = BertNormalizer(clean_text = True, handle_chinese_chars = False, strip_accents = None, lowercase = True)

    def to_trad(self):
        """Convert simplified to traditional"""
        converter = opencc.OpenCC('s2t.json')
        self.text = converter.convert(self.text)
    
    def punctuations(self):
        for uchar in self.text:
            ucode = ord(uchar)
            if ucode == 8943: # '⋯'
                for i in range(3):
                    self.newstring += chr(46)
            else:
                if ucode == 12288:
                    ucode = 32
                elif 65281 <= ucode <= 65374:
                    ucode -= 65248
                elif ucode == 8943: # '⋯'
                    ucode 
                self.newstring += chr(ucode)              

    def normalise(self):
        self.to_trad()
        self.text = self.normalizer.normalize_str(self.text)
        self.punctuations()
        return self.newstring

# Load Tokenizers
PRETRAINED_MODEL_NAME = "bert-base-chinese" 

# for Chinese, use tokenizer from bert-base-chinese
zhchar_tokeniser = BertTokenizer.from_pretrained(PRETRAINED_MODEL_NAME)

# import cantonese character piece tokeniser
yuechar_tokeniser = spm.SentencePieceProcessor()
yuechar_tokeniser.load('assets/char.model')

# Tokenizer functions
def t_zhchar_encode_0(inp):
    """Encoding Chinese texts using character piece tokenizer"""
    zh_encoded = [1] + zhchar_tokeniser.encode((inp[0].decode('UTF-8')), add_special_tokens=False) + [2]
    zh_encoded = tf.expand_dims(zh_encoded, 0)
    return tf.cast(zh_encoded, tf.int64)

def zhchar_encode_0(zh_t):
    zh_indices = t_zhchar_encode_0(zh_t)
    return zh_indices

@tf.function
def tf_zhchar_encode_0(zh_t):
    return tf.numpy_function(zhchar_encode_0, [zh_t], [tf.int64][0])

def t_yuechar_encode_0(inp):
    """Encoding Cantonese texts using character piece tokenizer"""
    yue_encoded = yuechar_tokeniser.EncodeAsIds(inp[0].decode('utf-8'))
    encode_array = [1] + yue_encoded[1:] + [2] # adding sos and eos tags
    return tf.cast(encode_array, tf.int64)

def yuechar_encode_0(yue_t):
    yue_indices = t_yuechar_encode_0(yue_t)
    return yue_indices

@tf.function
def tf_yuechar_encode_0(yue_t):
    return tf.numpy_function(yuechar_encode_0, [yue_t], [tf.int64][0])

def t_yuechar_decode_0(encoded):
    decode_array = []
    for inp in encoded:
        arr = inp.tolist()
        yue_decoded = yuechar_tokeniser.decode(arr)
        decode_array.append(yue_decoded)
    decoded = tf.ragged.constant(decode_array) # wrap in a ragged tensor
    return decoded

@tf.function
def tf_yuechar_decode_0(encoded):
    return tf.numpy_function(t_yuechar_decode_0, [encoded], [tf.string][0])

def t_yuechar_lookup(encoded):
    """Decoding Cantonese texts using character piece tokenizer"""
    decode_array = []
    for inp in encoded:
        arr = inp.tolist()
        yue_decoded = [yuechar_tokeniser.decode(i) for i in arr]
        decode_array.append(yue_decoded)
    decoded = tf.constant(decode_array) # wrap in a ragged tensor
    return decoded
    
@tf.function
def tf_yuechar_lookup(encoded):
    return tf.numpy_function(t_yuechar_lookup, [encoded], [tf.string][0])

MAX_TOKENS = 128

# Positional Encoding
def get_angles(pos, i, d_model):
    angle_rates = 1 / np.power(10000, (2*(i//2)) / np.float32(d_model))
    return pos * angle_rates

def positional_encoding(position, d_model):
    angle_rads = get_angles(np.arange(position)[:, np.newaxis],
                            np.arange(d_model)[np.newaxis, :],
                            d_model)
    # apply sin to even indices in the array; 2i
    angle_rads[:, 0::2] = np.sin(angle_rads[:, 0::2])
    
    # apply cos to odd indicies in the array; 2i + 1
    angle_rads[:, 1::2] = np.cos(angle_rads[:, 1::2])
    
    pos_encoding = angle_rads[np.newaxis, ...]
    
    return tf.cast(pos_encoding, dtype=tf.float32)

# Masking
def create_padding_mask(seq):
    seq = tf.cast(tf.math.equal(seq, 0), tf.float32)
    
    # add extra dimensions to add the padding to the attention logits
    return seq[:, tf.newaxis, tf.newaxis, :] # (batch_size, 1, 1, seq_len)

def create_look_ahead_mask(size):
    mask = 1 - tf.linalg.band_part(tf.ones((size, size)), -1, 0)
    return mask # (seq_len, seq_len)

# Attention
def scaled_dot_product_attention(q, k, v, mask):
    """Calculate the attention weights.
    q, k, v must have matching leading dimensions.
    k, v must have matching penultimate dimension, i,e.: seq_len_k = seq_len_v.
    The mask has different shapes depending on its type (padding or look-ahead)
    but it must be broadcastable for addition.
    
    Args:
        q: query shape == (..., seq_len_q, depth)
        k: key shape == (..., seq_len_k, depth)
        v: value shape == (..., seq_len_v, depth_v)
        mask: Float tensor with shape broadcastable to (..., seq_len_q, seq_len_k).
        Defaults to Nonw.
        
    Returns:
        output, attention_weights
    """
    matmul_qk = tf.matmul(q, k, transpose_b=True) # (..., seq_len_q, seq_len_k)
    
    # scale matmul_qk
    dk = tf.cast(tf.shape(k)[-1], tf.float32)
    scaled_attention_logits = matmul_qk / tf.math.sqrt(dk)
    
    # add the mask to the scaled tensor
    if mask is not None:
        scaled_attention_logits += (mask * -1e9)
    
    # softmax is normalised on the last axis (seq_len_k) so that the scores add up to 1
    attention_weights = tf.nn.softmax(scaled_attention_logits, axis=-1) # (..., seq_len_q, seq_len_k)
    
    output = tf.matmul(attention_weights, v) # (..., seq_len_q, depth_v)
    
    return output, attention_weights

class MultiHeadAttention(tf.keras.layers.Layer):
    def __init__(self,*, d_model, num_heads):
        super(MultiHeadAttention, self).__init__()
        self.num_heads = num_heads
        self.d_model = d_model

        assert d_model % self.num_heads == 0

        self.depth = d_model // self.num_heads

        self.wq = tf.keras.layers.Dense(d_model)
        self.wk = tf.keras.layers.Dense(d_model)
        self.wv = tf.keras.layers.Dense(d_model)

        self.dense = tf.keras.layers.Dense(d_model)

    def split_heads(self, x, batch_size):
        """Split the last dimension into (num_heads, depth).
        Transpose the result such that the shape is (batch_size, num_heads, seq_len, depth)
        """
        x = tf.reshape(x, (batch_size, -1, self.num_heads, self.depth))
        return tf.transpose(x, perm=[0, 2, 1, 3])

    def call(self, v, k, q, mask):
        batch_size = tf.shape(q)[0]

        q = self.wq(q)  # (batch_size, seq_len, d_model)
        k = self.wk(k)  # (batch_size, seq_len, d_model)
        v = self.wv(v)  # (batch_size, seq_len, d_model)

        q = self.split_heads(q, batch_size)  # (batch_size, num_heads, seq_len_q, depth)
        k = self.split_heads(k, batch_size)  # (batch_size, num_heads, seq_len_k, depth)
        v = self.split_heads(v, batch_size)  # (batch_size, num_heads, seq_len_v, depth)

        # scaled_attention.shape == (batch_size, num_heads, seq_len_q, depth)
        # attention_weights.shape == (batch_size, num_heads, seq_len_q, seq_len_k)
        scaled_attention, attention_weights = scaled_dot_product_attention(
            q, k, v, mask)

        scaled_attention = tf.transpose(scaled_attention, perm=[0, 2, 1, 3])  # (batch_size, seq_len_q, num_heads, depth)

        concat_attention = tf.reshape(scaled_attention,
                                      (batch_size, -1, self.d_model))  # (batch_size, seq_len_q, d_model)

        output = self.dense(concat_attention)  # (batch_size, seq_len_q, d_model)

        return output, attention_weights

# FFN
def point_wise_feed_forward_network(d_model, dff):
    return tf.keras.Sequential([
        tf.keras.layers.Dense(dff, activation='relu'), # (batch_size, seq_len, dff)
        tf.keras.layers.Dense(d_model) # (batch_size, seq_len, d_model)
    ])

# Encoding Layer
class EncoderLayer(tf.keras.layers.Layer):
    def __init__(self,*, d_model, num_heads, dff, rate=0.1):
        super(EncoderLayer, self).__init__()

        self.mha = MultiHeadAttention(d_model=d_model, num_heads=num_heads)
        self.ffn = point_wise_feed_forward_network(d_model, dff)

        # normalise layer
        self.layernorm1 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        self.layernorm2 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        
        # apply dropout
        self.dropout1 = tf.keras.layers.Dropout(rate)
        self.dropout2 = tf.keras.layers.Dropout(rate)

    def call(self, x, training, mask):

        attn_output, _ = self.mha(x, x, x, mask)  # (batch_size, input_seq_len, d_model)
        attn_output = self.dropout1(attn_output, training=training)
        out1 = self.layernorm1(x + attn_output)  # (batch_size, input_seq_len, d_model)

        ffn_output = self.ffn(out1)  # (batch_size, input_seq_len, d_model)
        ffn_output = self.dropout2(ffn_output, training=training)
        out2 = self.layernorm2(out1 + ffn_output)  # (batch_size, input_seq_len, d_model)

        return out2

# Decoding Layer
class DecoderLayer(tf.keras.layers.Layer):
    def __init__(self, *, d_model, num_heads, dff, rate=0.1):
        super(DecoderLayer, self).__init__()
        
        # multihead attention
        self.mha1 = MultiHeadAttention(d_model=d_model, num_heads=num_heads)
        self.mha2 = MultiHeadAttention(d_model=d_model, num_heads=num_heads)
        
        # point wise feed forward network
        self.ffn = point_wise_feed_forward_network(d_model, dff)
        
        # normalised layers
        self.layernorm1 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        self.layernorm2 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        self.layernorm3 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        
        # apply dropout
        self.dropout1 = tf.keras.layers.Dropout(rate)
        self.dropout2 = tf.keras.layers.Dropout(rate)
        self.dropout3 = tf.keras.layers.Dropout(rate)
        
    def call(self, x, enc_output, training, look_ahead_mask, padding_mask):
        # enc_output.shape == (batch_size, input_seq_len, d_model)
        
        attn1, attn_weights_block1 = self.mha1(x, x, x, look_ahead_mask) # (batch_size, target_seq_len, d_model)
        attn1 = self.dropout1(attn1, training=training)
        out1 = self.layernorm1(attn1 + x)
        
        attn2, attn_weights_block2 = self.mha2(enc_output, enc_output, out1, padding_mask) # (batch_size, target_seq_len, d_model)
        attn2 = self.dropout2(attn2, training=training)
        out2 = self.layernorm2(attn2 + out1)
        
        ffn_output = self.ffn(out2)
        ffn_output = self.dropout3(ffn_output, training=training)
        out3 = self.layernorm3(ffn_output + out2)
        
        return out3, attn_weights_block1, attn_weights_block2
                
# Encoder
class Encoder(tf.keras.layers.Layer):
    def __init__(self, *, num_layers, d_model, num_heads, dff, input_vocab_size, rate=0.1):
        super(Encoder, self).__init__()
        
        self.d_model = d_model
        self.num_layers = num_layers
        
        self.embedding = tf.keras.layers.Embedding(input_vocab_size, d_model)
        self.pos_encoding = positional_encoding(MAX_TOKENS, self.d_model)
        
        self.enc_layers = [
            EncoderLayer(d_model=d_model, num_heads=num_heads, dff=dff, rate=rate) 
            for _ in range(num_layers)]
        
        self.dropout = tf.keras.layers.Dropout(rate)
    
    def call(self, x, training, mask):
        
        seq_len = tf.shape(x)[1]
        
        # add embedding and position encoding
        x = self.embedding(x) # (batch_size, input_seq_len, d_model)
        x *= tf.math.sqrt(tf.cast(self.d_model, tf.float32))
        x += self.pos_encoding[:, :seq_len, :]
        
        x = self.dropout(x, training=training)
        
        for i in range(self.num_layers):
            x = self.enc_layers[i](x, training, mask)
            
        return x 

# Decoder
class Decoder(tf.keras.layers.Layer):
    def __init__(self, *, num_layers, d_model, num_heads, dff, target_vocab_size, rate=0.1):
        super(Decoder, self).__init__()
        
        self.d_model = d_model
        self.num_layers = num_layers
        
        self.embedding = tf.keras.layers.Embedding(target_vocab_size, d_model)
        self.pos_encoding = positional_encoding(MAX_TOKENS, d_model)
        
        self.dec_layers = [
            DecoderLayer(d_model=d_model, num_heads=num_heads, dff=dff, rate=rate)
            for _ in range(num_layers)]
        self.dropout = tf.keras.layers.Dropout(rate)
        
    def call(self, x, enc_output, training, look_ahead_mask, padding_mask):
        
        seq_len = tf.shape(x)[1]
        attention_weights = {}
        
        x = self.embedding(x)
        x *= tf.math.sqrt(tf.cast(self.d_model, tf.float32))
        x += self.pos_encoding[:, :seq_len, :]
        
        x = self.dropout(x, training=training)
        
        for i in range(self.num_layers):
            x, block1, block2 = self.dec_layers[i](
                x, enc_output, training, look_ahead_mask, padding_mask)
            
            attention_weights[f'decoder_layer{i+1}_block1'] = block1
            attention_weights[f'decoder_layer{i+1}_block2'] = block2
            
        return x, attention_weights

# Transformer
class Transformer(tf.keras.Model):
    def __init__(self, *, num_layers, d_model, num_heads, dff, input_vocab_size, target_vocab_size, rate=0.1):
        super().__init__()
        self.encoder = Encoder(num_layers=num_layers, d_model=d_model, num_heads=num_heads,
        dff=dff, input_vocab_size=input_vocab_size, rate=rate)

        self.decoder = Decoder(num_layers=num_layers, d_model=d_model, num_heads=num_heads,
        dff=dff, target_vocab_size=target_vocab_size, rate=rate)

        self.final_layer = tf.keras.layers.Dense(target_vocab_size)
    
    def call(self, inputs, training):
        # Passing all inputs in the first argument for keras models
        inp, tar = inputs

        padding_mask, look_ahead_mask = self.create_masks(inp, tar)

        enc_output = self.encoder(inp, training, padding_mask)

        # dec_output.shape == (batch_size, tar_seq_len, d_model)
        dec_output, attention_weights = self.decoder(
            tar, enc_output, training, look_ahead_mask, padding_mask)
        
        final_output = self.final_layer(dec_output)

        return final_output, attention_weights
    
    def create_masks(self, inp, tar):
        # Encoder padding mask, also used in the 2nd attention block in the decoder
        padding_mask = create_padding_mask(inp)

        # Used in the 1st attention block in the decoder
        # It is used to pad and mask future tokens in the input received by the decoder
        look_ahead_mask = create_look_ahead_mask(tf.shape(tar)[1])
        dec_target_padding_mask = create_padding_mask(tar)
        look_ahead_mask = tf.maximum(dec_target_padding_mask, look_ahead_mask)

        return padding_mask, look_ahead_mask
        
transformer = Transformer(
    num_layers=num_layers,
    d_model=d_model,
    num_heads=num_heads,
    dff=dff,
    input_vocab_size=zhchar_tokeniser.vocab_size,
    target_vocab_size=yuechar_tokeniser.vocab_size(),
    rate=dropout_rate
)

# create the checkpoint path and the checkpoint manager
# the manager will be used to save checkpoints every n epochs
checkpoint_path = 'chkpt'

ckpt = tf.train.Checkpoint(transformer=transformer)#, optimizer=optimizer)

ckpt_manager = tf.train.CheckpointManager(ckpt, checkpoint_path, max_to_keep=5)

# if a checkpoint exists, restore the latest checkpoint
if ckpt_manager.latest_checkpoint:
    ckpt.restore(ckpt_manager.latest_checkpoint)

    last_epoch = int(ckpt_manager.latest_checkpoint.split("-")[-1])*10
    print(f'Transformer model is loaded successfully.')
else:
    last_epoch = 0
    print("Transformer model not found.")

print("Loading Translator...")
# Translator
class Translator(tf.Module):
    def __init__(self, transformer):
        self.transformer = transformer
    
    def __call__(self, sentence, max_length=MAX_TOKENS):
        assert isinstance(sentence, tf.Tensor)
        if len(sentence.shape) == 0:
            sentence = sentence[tf.newaxis]

        # chinese input
        sentence = tf_zhchar_encode_0(sentence)

        encoder_input = sentence
        encoder_input = tf.ensure_shape(encoder_input, [1, None])

        # cantonese output
        # get sos and eos tags
        start_end = tf_yuechar_encode_0(tf.constant(['']))
        start = start_end[0][tf.newaxis]
        end = start_end[1][tf.newaxis]

        # use 'tf.TensorArray' instead of python list so that the dynamic-loop
        # can be traced by 'tf.function'
        output_array = tf.TensorArray(dtype=tf.int64, size=0, dynamic_size=True)
        output_array = output_array.write(0,start)

        for i in tf.range(max_length):
            output = tf.transpose(output_array.stack())
            output = tf.ensure_shape(output, [1, None])
            
            predictions, _ = self.transformer([encoder_input, output], training=False)

            # selecting the last token from the seq_len dimension
            predictions = predictions[:, -1:, :] # (batch_size, 1, vocab_size)

            predicted_id = tf.argmax(predictions, axis=-1)

            # concatenate the predicted_id to the output which is given to the decoder as its input
            output_array = output_array.write(i+1, predicted_id[0])

            if predicted_id == end:
                break

        output = tf.transpose(output_array.stack())

        #output.shape (1, tokens)
        text = tf_yuechar_decode_0(output)[0]

        tokens = tf_yuechar_lookup(output)[0]

        # recalculating the attention_weights outside the loop
        # tf.function does not allow calculating on the last iteration of the loop
        _, attention_weights = self.transformer([encoder_input, output[:,:-1]], training=False)

        return text, tokens, attention_weights

char_translator = Translator(transformer)

class ExportTranslator(tf.Module):
  def __init__(self, translator):
    self.translator = translator

  @tf.function(input_signature= [tf.TensorSpec(shape=[], dtype=tf.string)])
  def __call__(self, sentence):
    (result,
     tokens,
     attention_weights) = self.translator(sentence, max_length=MAX_TOKENS)

    return result

charpiece = ExportTranslator(char_translator)
print("Translator loaded successfully!")

while True:
    inp = input("Input Mandarin text: ")
    print(f"Mandarin text: {inp}")
    if inp.lower() != 'quit':
        # n = Normaliser(inp)
        # ninp = n.normalise()
        # print(f"Cantonese translation : {charpiece(ninp).numpy().decode('UTF-8')}")
        print(f"Cantonese translation : {charpiece(inp).numpy().decode('UTF-8')}")
    else:
        break