#!/usr/bin/env python3
"""Export 3D-Speaker models to ONNX format for use with onnx_embedder.py.

Requires: pip install speakerlab torch modelscope

Usage:
    python -m diarization.export_onnx                        # export default (eres2net_large)
    python -m diarization.export_onnx --model campplus        # export CAM++
    python -m diarization.export_onnx --model eres2netv2      # export ERes2NetV2
    python -m diarization.export_onnx --list                  # list available models
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Model configs: name → (modelscope_id, revision, model_class_path, init_kwargs, embed_dim)
MODEL_CONFIGS = {
    "eres2net_large": {
        "modelscope_id": "iic/speech_eres2net_large_sv_zh-cn_3dspeaker_16k",
        "revision": "v1.0.0",
        "onnx_filename": "eres2net_large.onnx",
        "embed_dim": 512,
        "model_init": {
            "class": "ERes2Net",
            "kwargs": {
                "feat_dim": 80,
                "embedding_size": 512,
                "m_channels": 64,
            },
        },
    },
    "eres2netv2": {
        "modelscope_id": "iic/speech_eres2netv2_sv_zh-cn_16k-common",
        "revision": None,
        "onnx_filename": "eres2netv2.onnx",
        "embed_dim": 192,
        "model_init": {
            "class": "ERes2NetV2",
            "kwargs": {
                "feat_dim": 80,
                "embedding_size": 192,
            },
        },
    },
    "campplus": {
        "modelscope_id": "iic/speech_campplus_sv_zh-cn_16k-common",
        "revision": "v1.0.0",
        "onnx_filename": "campplus.onnx",
        "embed_dim": 512,
        "model_init": {
            "class": "CAMPPlus",
            "kwargs": {
                "feat_dim": 80,
                "embedding_size": 512,
            },
        },
    },
    # Language identification models
    "campplus_lid": {
        "modelscope_id": "iic/speech_campplus_lre_en-cn_16k",
        "revision": None,
        "onnx_filename": "campplus_lid.onnx",
        "embed_dim": None,  # LID model outputs logits, not embeddings
        "is_lid": True,
        "model_init": {
            "class": "CAMPPlus",
            "kwargs": {
                "feat_dim": 80,
                "embedding_size": 512,
            },
        },
    },
    "eres2net_lid": {
        "modelscope_id": "iic/speech_eres2net_base_lre_en-cn_16k",
        "revision": None,
        "onnx_filename": "eres2net_lid.onnx",
        "embed_dim": None,
        "is_lid": True,
        "model_init": {
            "class": "ERes2Net",
            "kwargs": {
                "feat_dim": 80,
                "embedding_size": 192,
            },
        },
    },
}

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "3dspeaker"


def export_model(
    model_name: str,
    output_path: Path | None = None,
    cache_dir: Path | None = None,
) -> Path:
    """Export a 3D-Speaker model to ONNX format.

    Downloads the pretrained weights from ModelScope, instantiates the model,
    and exports via torch.onnx.export with dynamic axes.
    """
    import torch
    from modelscope.hub.snapshot_download import snapshot_download

    if model_name not in MODEL_CONFIGS:
        raise ValueError(f"Unknown model: {model_name}. Available: {list(MODEL_CONFIGS.keys())}")

    config = MODEL_CONFIGS[model_name]
    cache = cache_dir or DEFAULT_CACHE_DIR
    out_dir = cache / model_name
    out_dir.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        output_path = out_dir / config["onnx_filename"]

    if output_path.exists():
        print(f"ONNX model already exists: {output_path}")
        return output_path

    print(f"Downloading {model_name} from ModelScope ({config['modelscope_id']})...")
    model_dir = snapshot_download(
        config["modelscope_id"],
        revision=config.get("revision"),
    )
    model_dir = Path(model_dir)

    # Find the pretrained checkpoint
    ckpt_path = _find_checkpoint(model_dir)
    print(f"Found checkpoint: {ckpt_path}")

    # Instantiate the model
    model = _create_model(config)
    state_dict = torch.load(str(ckpt_path), map_location="cpu", weights_only=True)
    model.load_state_dict(state_dict)
    model.eval()

    # Export to ONNX
    dummy_input = torch.randn(1, 345, 80)  # ~3.5s of audio at 10ms frame shift
    print(f"Exporting to ONNX: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        dummy_input,
        str(output_path),
        export_params=True,
        opset_version=14,
        do_constant_folding=True,
        input_names=["feature"],
        output_names=["embedding"],
        dynamic_axes={
            "feature": {0: "batch_size", 1: "frame_num"},
            "embedding": {0: "batch_size"},
        },
    )

    # Verify the export
    _verify_export(output_path, dummy_input, model, config.get("embed_dim"))

    print(f"Export complete: {output_path}")
    print(f"  Embedding dim: {config['embed_dim']}")
    print(f"  File size: {output_path.stat().st_size / 1024 / 1024:.1f} MB")
    return output_path


def _find_checkpoint(model_dir: Path) -> Path:
    """Find the checkpoint file in a ModelScope download dir."""
    for pattern in ["*.ckpt", "*.bin", "*.pt", "*.pth", "pretrained_model/*.bin"]:
        matches = list(model_dir.glob(pattern))
        if matches:
            # Prefer the largest file (model weights, not config)
            return max(matches, key=lambda p: p.stat().st_size)

    raise FileNotFoundError(
        f"No checkpoint file found in {model_dir}. "
        f"Contents: {[p.name for p in model_dir.iterdir()]}"
    )


def _create_model(config: dict):
    """Instantiate the model class with the given config."""
    import torch.nn as nn

    model_info = config["model_init"]
    class_name = model_info["class"]
    kwargs = model_info["kwargs"]

    # Import from speakerlab
    if class_name == "ERes2Net":
        from speakerlab.models.eres2net.ERes2Net import ERes2Net
        return ERes2Net(**kwargs)
    elif class_name == "ERes2NetV2":
        from speakerlab.models.eres2net.ERes2NetV2 import ERes2NetV2
        return ERes2NetV2(**kwargs)
    elif class_name == "CAMPPlus":
        from speakerlab.models.campplus.DTDNN import CAMPPlus
        return CAMPPlus(**kwargs)
    else:
        raise ValueError(f"Unknown model class: {class_name}")


def _verify_export(
    onnx_path: Path,
    dummy_input,
    torch_model,
    embed_dim: int | None,
) -> None:
    """Verify ONNX output matches PyTorch output."""
    import onnxruntime
    import torch

    # PyTorch reference
    with torch.no_grad():
        ref_output = torch_model(dummy_input).numpy()

    # ONNX inference
    session = onnxruntime.InferenceSession(
        str(onnx_path),
        providers=["CPUExecutionProvider"],
    )
    onnx_output = session.run(None, {"feature": dummy_input.numpy()})[0]

    # Check shapes
    assert ref_output.shape == onnx_output.shape, (
        f"Shape mismatch: PyTorch {ref_output.shape} vs ONNX {onnx_output.shape}"
    )
    if embed_dim is not None:
        assert ref_output.shape[1] == embed_dim, (
            f"Embedding dim mismatch: expected {embed_dim}, got {ref_output.shape[1]}"
        )

    # Check values
    max_diff = float(abs(ref_output - onnx_output).max())
    print(f"  PyTorch vs ONNX max diff: {max_diff:.6f}")
    if max_diff > 1e-4:
        print(f"  WARNING: Large divergence ({max_diff:.4f}). Model may not work correctly.")
    else:
        print(f"  Verification passed (max diff: {max_diff:.6f})")


def main():
    parser = argparse.ArgumentParser(description="Export 3D-Speaker models to ONNX")
    parser.add_argument(
        "--model", "-m",
        choices=list(MODEL_CONFIGS.keys()),
        default="eres2net_large",
        help="Model to export (default: eres2net_large)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output path (default: ~/.cache/3dspeaker/<model>/<model>.onnx)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available models and exit",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Export all available models",
    )
    args = parser.parse_args()

    if args.list:
        print("Available models:")
        for name, config in MODEL_CONFIGS.items():
            print(f"  {name:20s}  {config['embed_dim']}-dim  ({config['modelscope_id']})")
        return

    if args.all:
        for name in MODEL_CONFIGS:
            print(f"\n{'='*60}")
            print(f"Exporting {name}...")
            print(f"{'='*60}")
            export_model(name)
        return

    export_model(args.model, args.output)


if __name__ == "__main__":
    main()
