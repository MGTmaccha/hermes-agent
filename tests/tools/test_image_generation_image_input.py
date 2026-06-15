"""Tests for image-to-image / edit input on tools/image_generation_tool.py.

Covers the ``image_urls`` parameter added to ``image_generate``:

  * input-ref resolution (http(s) URL / data: URI / local path → data URI),
  * edit-endpoint routing (models with an ``edit`` entry switch model + payload),
  * the fall-through when a model has no edit endpoint,
  * the end-to-end submit path (which model id and arguments reach FAL).

These are behavior/invariant tests — they assert *relationships* (the image
key lands in the payload, the submitted model id is the edit endpoint, a model
without edit is untouched), not frozen catalog snapshots, so they survive
catalog churn.
"""

from __future__ import annotations

import base64
import importlib
import json
import struct
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def image_tool():
    import tools.image_generation_tool as mod
    return importlib.reload(mod)


def _tiny_png_bytes() -> bytes:
    """A minimal valid 1x1 PNG (so MIME sniffing + Pillow open succeed)."""
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = b"\x00\xff\x00\x00"  # one filtered RGB pixel
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# ---------------------------------------------------------------------------
# Catalog invariants for edit endpoints
# ---------------------------------------------------------------------------

class TestEditCatalog:
    def test_edit_entries_have_required_keys(self, image_tool):
        for mid, meta in image_tool.FAL_MODELS.items():
            edit = meta.get("edit")
            if edit is None:
                continue
            assert isinstance(edit, dict), f"{mid}.edit must be a dict"
            for key in ("model", "image_key", "max_images"):
                assert key in edit, f"{mid}.edit missing {key!r}"
            assert isinstance(edit["model"], str) and edit["model"]
            assert isinstance(edit["image_key"], str) and edit["image_key"]

    def test_synthesized_edit_meta_lets_image_key_through(self, image_tool):
        """Every declared edit endpoint must whitelist its own image key, or
        _build_fal_payload would silently strip the reference images."""
        for mid, meta in image_tool.FAL_MODELS.items():
            if not meta.get("edit"):
                continue
            target = image_tool._resolve_edit_target(mid, ["http://x/y.png"])
            assert target is not None, f"{mid} should resolve an edit target"
            edit_model_id, edit_meta = target
            assert edit_model_id == meta["edit"]["model"]
            assert meta["edit"]["image_key"] in edit_meta["supports"]
            # Edits infer output size from the input — no forced size mapping.
            assert edit_meta["size_style"] == "none"
            # Editing never chains the upscaler.
            assert edit_meta["upscale"] is False

    def test_edit_supports_does_not_inherit_generate_only_keys(self, image_tool):
        """Regression: edit endpoints must declare their OWN supports, not
        inherit the generate whitelist. flux-2-pro/edit rejects
        num_inference_steps/guidance_scale that flux-2-pro generate accepts —
        if we inherited, those would 422 the edit call."""
        meta = image_tool.FAL_MODELS["fal-ai/flux-2-pro"]
        _, edit_meta = image_tool._resolve_edit_target(
            "fal-ai/flux-2-pro", ["http://x/y.png"]
        )
        assert "num_inference_steps" not in edit_meta["supports"]
        assert "guidance_scale" not in edit_meta["supports"]
        # but the generate endpoint DOES accept them (proves divergence).
        assert "num_inference_steps" in meta["supports"]

    def test_multi_flag_matches_image_key_plurality(self, image_tool):
        """Single-image endpoints use the singular ``image_url`` key; multi-
        image endpoints use ``image_urls``."""
        for mid, meta in image_tool.FAL_MODELS.items():
            edit = meta.get("edit")
            if not edit:
                continue
            _, edit_meta = image_tool._resolve_edit_target(mid, ["http://x/y.png"])
            if edit_meta["_image_key"] == "image_url":
                assert edit_meta["_multi"] is False, f"{mid} singular key must be _multi=False"
                assert edit["max_images"] == 1
            else:
                assert edit_meta["_image_key"] == "image_urls"


# ---------------------------------------------------------------------------
# Edit-target routing decision
# ---------------------------------------------------------------------------

class TestResolveEditTarget:
    def test_no_images_means_no_edit(self, image_tool):
        assert image_tool._resolve_edit_target("fal-ai/nano-banana-pro", []) is None

    def test_model_without_edit_endpoint_returns_none(self, image_tool):
        # z-image/turbo and ideogram/v3 deliberately have no edit endpoint.
        assert image_tool._resolve_edit_target(
            "fal-ai/z-image/turbo", ["http://x/y.png"]
        ) is None
        assert image_tool._resolve_edit_target(
            "fal-ai/ideogram/v3", ["http://x/y.png"]
        ) is None

    def test_unknown_model_returns_none(self, image_tool):
        assert image_tool._resolve_edit_target(
            "fal-ai/does-not-exist", ["http://x/y.png"]
        ) is None


# ---------------------------------------------------------------------------
# Input image resolution
# ---------------------------------------------------------------------------

class TestResolveInputImages:
    def test_http_url_passthrough(self, image_tool):
        assert image_tool._resolve_input_images("https://a/b.png") == ["https://a/b.png"]

    def test_data_uri_passthrough(self, image_tool):
        uri = "data:image/png;base64,ZZ"
        assert image_tool._resolve_input_images([uri]) == [uri]

    def test_none_is_empty(self, image_tool):
        assert image_tool._resolve_input_images(None) == []

    def test_missing_local_file_dropped(self, image_tool):
        assert image_tool._resolve_input_images(["/no/such/file.png"]) == []

    def test_max_images_truncates(self, image_tool):
        urls = ["https://a/1.png", "https://a/2.png", "https://a/3.png"]
        assert image_tool._resolve_input_images(urls, max_images=2) == urls[:2]

    def test_local_file_encoded_to_data_uri(self, image_tool, tmp_path):
        p = tmp_path / "ref.png"
        p.write_bytes(_tiny_png_bytes())
        out = image_tool._resolve_input_images([str(p)])
        assert len(out) == 1
        assert out[0].startswith("data:image/")
        assert ";base64," in out[0]
        # round-trips to non-empty bytes
        b64 = out[0].split(";base64,", 1)[1]
        assert base64.b64decode(b64)


# ---------------------------------------------------------------------------
# End-to-end submit path (mocked FAL)
# ---------------------------------------------------------------------------

class _FakeHandler:
    def __init__(self, result):
        self._result = result

    def get(self):
        return self._result


def _patch_backend(image_tool, monkeypatch, captured):
    monkeypatch.setattr(image_tool, "fal_key_is_configured", lambda: True)
    monkeypatch.setattr(image_tool, "_resolve_managed_fal_gateway", lambda: None)

    def fake_submit(model, arguments=None, **kw):
        captured["model"] = model
        captured["arguments"] = arguments
        return _FakeHandler({"images": [{"url": "https://out/img.png", "width": 1, "height": 1}]})

    monkeypatch.setattr(image_tool, "_submit_fal_request", fake_submit)


class TestEndToEndSubmit:
    def test_text_to_image_untouched_without_images(self, image_tool, monkeypatch):
        captured = {}
        _patch_backend(image_tool, monkeypatch, captured)
        monkeypatch.setattr(
            image_tool, "_resolve_fal_model",
            lambda: ("fal-ai/nano-banana-pro", image_tool.FAL_MODELS["fal-ai/nano-banana-pro"]),
        )
        out = json.loads(image_tool.image_generate_tool(prompt="a cat"))
        assert out["success"] is True
        # No edit routing: the generate model id is submitted, no image key.
        assert captured["model"] == "fal-ai/nano-banana-pro"
        assert "image_urls" not in captured["arguments"]

    def test_edit_routing_switches_model_and_adds_images(self, image_tool, monkeypatch):
        captured = {}
        _patch_backend(image_tool, monkeypatch, captured)
        monkeypatch.setattr(
            image_tool, "_resolve_fal_model",
            lambda: ("fal-ai/nano-banana-pro", image_tool.FAL_MODELS["fal-ai/nano-banana-pro"]),
        )
        out = json.loads(image_tool.image_generate_tool(
            prompt="make it night",
            image_urls=["https://in/ref.png"],
        ))
        assert out["success"] is True
        assert out.get("edited") is True
        # Routed to the edit endpoint, with the reference image in the payload.
        assert captured["model"] == "fal-ai/nano-banana-pro/edit"
        assert captured["arguments"]["image_urls"] == ["https://in/ref.png"]
        assert captured["arguments"]["prompt"] == "make it night"

    def test_edit_payload_omits_forced_image_size(self, image_tool, monkeypatch):
        """Edits must not inject an explicit image_size/aspect_ratio — FAL
        infers output dims from the input image."""
        captured = {}
        _patch_backend(image_tool, monkeypatch, captured)
        monkeypatch.setattr(
            image_tool, "_resolve_fal_model",
            lambda: ("fal-ai/flux-2-pro", image_tool.FAL_MODELS["fal-ai/flux-2-pro"]),
        )
        image_tool.image_generate_tool(prompt="x", image_urls=["https://in/r.png"])
        # flux-2-pro/edit DOES accept image_size, but we don't force one.
        assert "image_size" not in captured["arguments"]

    def test_flux2_pro_edit_drops_generate_only_params(self, image_tool, monkeypatch):
        """num_inference_steps/guidance_scale must NOT reach flux-2-pro/edit
        even when passed as overrides (they'd 422)."""
        captured = {}
        _patch_backend(image_tool, monkeypatch, captured)
        monkeypatch.setattr(
            image_tool, "_resolve_fal_model",
            lambda: ("fal-ai/flux-2-pro", image_tool.FAL_MODELS["fal-ai/flux-2-pro"]),
        )
        image_tool.image_generate_tool(
            prompt="x",
            image_urls=["https://in/r.png"],
            num_inference_steps=50,
            guidance_scale=4.5,
        )
        assert "num_inference_steps" not in captured["arguments"]
        assert "guidance_scale" not in captured["arguments"]

    def test_qwen_edit_uses_singular_image_url(self, image_tool, monkeypatch):
        """Qwen's edit endpoint takes a single ``image_url`` string, not a list."""
        captured = {}
        _patch_backend(image_tool, monkeypatch, captured)
        monkeypatch.setattr(
            image_tool, "_resolve_fal_model",
            lambda: ("fal-ai/qwen-image", image_tool.FAL_MODELS["fal-ai/qwen-image"]),
        )
        image_tool.image_generate_tool(
            prompt="x",
            image_urls=["https://in/a.png", "https://in/b.png"],
        )
        assert captured["model"] == "fal-ai/qwen-image-edit"
        # Singular string, first image only.
        assert captured["arguments"]["image_url"] == "https://in/a.png"
        assert "image_urls" not in captured["arguments"]

    def test_no_edit_model_surfaces_note_in_result(self, image_tool, monkeypatch):
        captured = {}
        _patch_backend(image_tool, monkeypatch, captured)
        monkeypatch.setattr(
            image_tool, "_resolve_fal_model",
            lambda: ("fal-ai/z-image/turbo", image_tool.FAL_MODELS["fal-ai/z-image/turbo"]),
        )
        out = json.loads(image_tool.image_generate_tool(
            prompt="a dog",
            image_urls=["https://in/ref.png"],
        ))
        assert out["success"] is True
        assert "note" in out and "does not support image" in out["note"]

    def test_images_on_model_without_edit_fall_back_to_text(self, image_tool, monkeypatch):
        captured = {}
        _patch_backend(image_tool, monkeypatch, captured)
        # z-image/turbo has no edit endpoint.
        monkeypatch.setattr(
            image_tool, "_resolve_fal_model",
            lambda: ("fal-ai/z-image/turbo", image_tool.FAL_MODELS["fal-ai/z-image/turbo"]),
        )
        out = json.loads(image_tool.image_generate_tool(
            prompt="a dog",
            image_urls=["https://in/ref.png"],
        ))
        assert out["success"] is True
        # Stays on the generate model; images dropped (no edit endpoint).
        assert captured["model"] == "fal-ai/z-image/turbo"
        assert "image_urls" not in captured["arguments"]

    def test_edit_does_not_chain_upscaler(self, image_tool, monkeypatch):
        """flux-2-pro has upscale=True for generate, but its edit endpoint must
        not chain the upscaler (would double-submit)."""
        captured = {}
        _patch_backend(image_tool, monkeypatch, captured)
        monkeypatch.setattr(
            image_tool, "_resolve_fal_model",
            lambda: ("fal-ai/flux-2-pro", image_tool.FAL_MODELS["fal-ai/flux-2-pro"]),
        )
        # If the upscaler were chained, _submit_fal_request would be called a
        # second time with the clarity-upscaler model and overwrite captured.
        out = json.loads(image_tool.image_generate_tool(
            prompt="add flames",
            image_urls=["https://in/ref.png"],
        ))
        assert out["success"] is True
        assert captured["model"] == "fal-ai/flux-2-pro/edit"  # not the upscaler

    def test_local_path_reaches_fal_as_data_uri(self, image_tool, monkeypatch, tmp_path):
        captured = {}
        _patch_backend(image_tool, monkeypatch, captured)
        monkeypatch.setattr(
            image_tool, "_resolve_fal_model",
            lambda: ("fal-ai/flux-2/klein/9b", image_tool.FAL_MODELS["fal-ai/flux-2/klein/9b"]),
        )
        p = tmp_path / "ref.png"
        p.write_bytes(_tiny_png_bytes())
        out = json.loads(image_tool.image_generate_tool(
            prompt="stylize",
            image_urls=[str(p)],
        ))
        assert out["success"] is True
        assert captured["model"] == "fal-ai/flux-2/klein/9b/edit"
        imgs = captured["arguments"]["image_urls"]
        assert len(imgs) == 1 and imgs[0].startswith("data:image/")
