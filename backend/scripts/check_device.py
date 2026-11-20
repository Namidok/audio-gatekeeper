import logging

import torch

from app.ai_pipeline import describe_device, pick_device

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
print("PyTorch version :", torch.__version__)
print("CUDA available  :", torch.cuda.is_available())
print("MPS available   :", torch.backends.mps.is_available())
print("Selected device :", describe_device(pick_device()))
