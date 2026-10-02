import os
from functools import lru_cache

from app.config import settings

# All models we use are already downloaded/cached locally - without this, every
# cold load makes a HEAD request to huggingface.co to check for updates, and that
# request has hung indefinitely (no timeout) at least once already, stalling
# whatever local/paid pipeline stage was loading the model at the time.
os.environ.setdefault("HF_HUB_OFFLINE", "1")


@lru_cache(maxsize=1)
def _text_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.text_embedding_model)


@lru_cache(maxsize=1)
def _visual_model():
    import torch
    from transformers import AutoModel, AutoProcessor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModel.from_pretrained(settings.visual_embedding_model).to(device).eval()
    processor = AutoProcessor.from_pretrained(settings.visual_embedding_model)
    return model, processor, device


def text_embedding_model_id() -> str:
    """Identifier stored alongside every text vector - lets a future migration find
    and re-embed vectors from a retired model instead of silently mixing spaces."""
    return settings.text_embedding_model


def visual_embedding_model_id() -> str:
    return settings.visual_embedding_model


def embed_text(text: str) -> list[float]:
    model = _text_model()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


def embed_image(image_path: str) -> list[float]:
    import torch
    from PIL import Image

    model, processor, device = _visual_model()
    image = Image.open(image_path).convert("RGB")
    inputs = processor(images=image, return_tensors="pt").to(device)
    with torch.no_grad():
        output = model.get_image_features(**inputs)
        features = output.pooler_output if hasattr(output, "pooler_output") else output
        features = features / features.norm(dim=-1, keepdim=True)
    return features.squeeze(0).cpu().tolist()


def embed_text_for_visual_search(text: str) -> list[float]:
    import torch

    model, processor, device = _visual_model()
    # SigLIP was trained with fixed-length padding for text - unlike CLIP, using
    # variable-length padding here silently degrades text-embedding quality.
    # The text tower takes 64 tokens at most; a longer description would raise, so the
    # tail is cut off - the start of a description is what says what the picture shows.
    inputs = processor(text=[text], padding="max_length", truncation=True, max_length=64, return_tensors="pt").to(device)
    with torch.no_grad():
        output = model.get_text_features(**inputs)
        features = output.pooler_output if hasattr(output, "pooler_output") else output
        features = features / features.norm(dim=-1, keepdim=True)
    return features.squeeze(0).cpu().tolist()
