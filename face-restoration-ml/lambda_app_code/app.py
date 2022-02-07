import argparse
import io
import os
import json
import sys

python_path = os.environ["PYTHON_LIB_FILE_PATH"]
sys.path.append(python_path)

import torch
import boto3
import numpy as np

from PIL import Image
from botocore.client import Config
from gfpgan import GFPGANer

AWS_S3__IN_BUCKET_NAME = os.environ['AWS_S3_IN_BUCKET_NAME']
AWS_S3__OUT_BUCKET_NAME = os.environ['AWS_S3_OUT_BUCKET_NAME']
MODEL_FILE_PATH = os.environ['MODEL_FILE_PATH']


def lambda_handler(event, context):
    """Inference lambda demo for GFPGAN.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--upscale', type=int, default=2, help='The final upsampling scale of the image')
    parser.add_argument('--arch', type=str, default='clean', help='The GFPGAN architecture. Option: clean | original')
    parser.add_argument('--channel', type=int, default=2, help='Channel multiplier for large networks of StyleGAN2')
    parser.add_argument('--model_path', type=str, default=f'{MODEL_FILE_PATH}/cpu.pth')
    parser.add_argument('--bg_upsampler', type=str, default='realesrgan', help='background upsampler')
    parser.add_argument('--bg_tile', type=int, default=400,
                        help='Tile size for background sampler, 0 for no tile during testing')
    parser.add_argument('--test_path', type=str, default='inputs/whole_imgs', help='Input folder')
    parser.add_argument('--suffix', type=str, default=None, help='Suffix of the restored faces')
    parser.add_argument('--only_center_face', action='store_true', help='Only restore the center face')
    parser.add_argument('--aligned', action='store_true', help='Input are aligned faces')
    parser.add_argument('--paste_back', action='store_false', help='Paste the restored faces back to images')
    parser.add_argument('--save_root', type=str, default='results', help='Path to save root')
    parser.add_argument('--ext', type=str, default='auto',
                        help='''Image extension. Options: auto | jpg | png,
                        auto means using the same extension as inputs''')

    config = Config(connect_timeout=900, retries={'max_attempts': 0})
    s3 = boto3.resource('s3')
    bucket_in = s3.Bucket(AWS_S3__IN_BUCKET_NAME)

    try:
        # https://stackoverflow.com/questions/36205481/read-file-content-from-s3-bucket-with-boto3
        file_name = str(event.get("Records")[0].get("s3").get("object").get("key"))
    except:
        return None
    if file_name:
        s3_object = bucket_in.Object(file_name).get()

        file_object = s3_object['Body']
        image_stream = Image.open(file_object)
        input_img = np.array(image_stream)

        args = parser.parse_args()

        # background upsampler
        if args.bg_upsampler == 'realesrgan':
            if not torch.cuda.is_available():  # CPU
                # The unoptimized RealESRGAN is very slow on CPU. We do not use it.
                # If you really want to use it, please modify the corresponding codes.
                bg_upsampler = None
            else:
                from basicsr.archs.rrdbnet_arch import RRDBNet
                from realesrgan import RealESRGANer
                model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=2)
                bg_upsampler = RealESRGANer(
                    scale=2,
                    model_path=f'{MODEL_FILE_PATH}/gpu.pth',
                    model=model,
                    tile=args.bg_tile,
                    tile_pad=10,
                    pre_pad=0,
                    half=True)  # need to set False in CPU mode
        else:
            bg_upsampler = None

        # set up GFPGAN restorer
        restorer = GFPGANer(
            model_path=args.model_path,
            upscale=args.upscale,
            arch=args.arch,
            channel_multiplier=args.channel,
            bg_upsampler=bg_upsampler)

        # restore faces and background if necessary
        cropped_faces, restored_faces, restored_img = restorer.enhance(
            input_img, has_aligned=args.aligned, only_center_face=args.only_center_face, paste_back=args.paste_back)

        # save restored img to S3
        if restored_img is not None:
            # Numpy implicitly converts an RBG image to BGR.
            # https://stackoverflow.com/questions/4661557/pil-rotate-image-colors-bgr-rgb
            # May be needed when run locally
            # ::-1 inverts the order of the last dimension (channels).
            # rgb = restored_img[:, :, ::-1].copy()
            img = Image.fromarray(restored_img)
            out_img = io.BytesIO()
            img.save(out_img, format='png')
            out_img.seek(0)

            bucket_out = s3.Bucket(AWS_S3__OUT_BUCKET_NAME)
            bucket_out.put_object(
                Body=out_img,
                Key=file_name
            )
            return {
                'statusCode': 200,
                'body': json.dumps("SUCCESS")
            }
