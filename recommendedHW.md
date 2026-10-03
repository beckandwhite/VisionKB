# Recommended Vision Models

Tested / recommended for `vision_model` in `config.template.json`.

Tok/s figures are **approximate community benchmarks** for text-generation throughput
(the image-encode pass is a one-time ~0.2–2 s overhead per image and is not included).
Hardware FLOPS and bandwidth are manufacturer specs where available; estimates are marked †.

---

## Hardware comparison

| Hardware | FP32 TFLOPS | Mem bandwidth | LLM inference bottleneck |
|---|---|---|---|
| GTX 1050 Ti | 2.1 TFLOPS | 112 GB/s | Bandwidth-limited above ~2 B params |
| Mac mini M4 (10-core GPU) | ~4.0 TFLOPS † | 120 GB/s | Similar bandwidth to 1050 Ti but better compute efficiency + Neural Engine (38 TOPS) |
| MacBook Pro M5 Pro (15-core GPU, 48 GB) | ~22 TFLOPS † | ~273 GB/s † | Bandwidth-rich; 30 B models fit fully in-memory with room to spare |
| HP Pavilion x360 14-ek2xxx (Core 5 120U, Intel Graphics) | Not published | Up to 51.2 GB/s † shared RAM | Integrated GPU; Ollama used CPU only in the local test; 16 GB system RAM |

> † M4 GPU FP32 and M5 Pro figures are estimates. Apple does not publish exact FP32
> TFLOPS for GPU or Neural Engine separately; the real-world inference speed advantage
> of Apple Silicon comes primarily from the Neural Engine (INT8/FP16) and unified
> memory bandwidth, not raw FP32.
>
> † The HP's bandwidth is a theoretical dual-channel DDR4-3200 system-memory peak
> (2 × 64-bit channels); it is shared with the integrated GPU, not dedicated VRAM.
> No FP32 figure is listed for this integrated GPU.

---

## MacBook Pro M5 Pro — 15-core GPU, 48 GB unified memory

~273 GB/s memory bandwidth. 30 B models fit entirely in-memory; MLX format
exploits the Neural Engine for additional throughput.

| Model | Ollama tag | Model size | Est. tok/s | Notes |
|---|---|---|---|---|
| Muse Glimmer 30B (MLX) | `muse-glimmer:30b-mlx` | ~20 GB | ~30–50 tok/s | **Current model.** MLX-native; best description quality. |
| Qwen2-VL 7B | `qwen2-vl:7b` | ~5 GB | ~60–80 tok/s | Much faster if throughput matters more than quality. |

---

## Mac mini M4 — 16 GB unified memory

120 GB/s memory bandwidth. Practical limit is ~8 B models before memory pressure
causes slowdowns.

| Model | Ollama tag | Model size | Est. tok/s | Notes |
|---|---|---|---|---|
| Qwen2-VL 7B | `qwen2-vl:7b` | ~5 GB | ~20–30 tok/s | **Recommended.** Best quality/size ratio; strong at UI and scene description. |
| LLaVA-Llama 3 8B | `llava-llama3:8b` | ~5.5 GB | ~15–25 tok/s | Reliable all-rounder; well-tested on screenshot content. |
| Moondream 2 | `moondream2` | ~1.5 GB | ~50–70 tok/s | Fastest option; lower quality — good for a quick first pass. |

---

## NVIDIA GeForce GTX 1050 Ti — 4 GB VRAM

2.1 TFLOPS FP32, 112 GB/s bandwidth. The 4 GB VRAM hard-cap means anything
above ~3 B (Q4) will spill to CPU RAM and become 5–10× slower. Keep models
under ~2.5 GB on-GPU.

| Model | Ollama tag | VRAM | Est. tok/s | Notes |
|---|---|---|---|---|
| Moondream 2 | `moondream2` | ~1.5 GB | ~20–30 tok/s | **Recommended.** Purpose-built for constrained hardware; fits with headroom. |
| LLaVA-Phi-3 Mini | `llava-phi3` | ~2.5 GB | ~8–14 tok/s | Higher quality than Moondream (3.8 B Phi-3 base); slower due to size. |
| MiniCPM-V 2B | `minicpm-v:2b` | ~1.5 GB | ~20–28 tok/s | Strong quality for its size; alternative to Moondream if available in your registry. |

> **CUDA support note:** GTX 1050 Ti is Pascal (compute capability 6.1).
> Ollama supports it, but recent CUDA builds may drop Pascal. If the GPU is
> not detected, pin Ollama to an older release or run CPU-only.

---

## HP Pavilion x360 14-ek2xxx — Core 5 120U, Intel Graphics, 16 GB RAM

This Windows 11 laptop has a 10-core/12-thread Intel Core 5 120U, Intel integrated
graphics, and 16 GB DDR4-3200 RAM. It has no dedicated GPU memory. Although the
Intel GPU is visible through Vulkan, Ollama 0.30.7 ran the test below on 100% CPU;
the GPU was not used for inference.

| Local Ollama test | Result |
|---|---|
| Model | `gemma4:e4b` — 8 B parameters, Q4_K_M, vision-capable, ~9.5 GB |
| Cold model load | ~72.3 s |
| Prompt evaluation | 25 tokens in ~1.47 s |
| Text generation | 14 tokens in ~3.22 s — **~4.34 tok/s** |
| Runtime context | 2,048 tokens as reported by `ollama ps` |

This was one short text-only spot check; it did **not** measure image encoding or
vision-response latency. The tok/s result is for generated text only and is not a
direct comparison with the different models and hardware in the estimates above.
It indicates that this laptop can run a quantized 8 B model, but its measured rate
is below the GPU-backed estimates elsewhere in this guide; because those use
different models, this is only a directional comparison. A ~9.5 GB model also
leaves limited room in 16 GB of shared system memory. Prefer a smaller quantized
vision model for regular use unless a supported GPU backend is enabled.

---

## Notes

- All models use Ollama's `/api/generate` vision endpoint.
- Tok/s are for text generation only; image encoding adds ~0.2–2 s per image
  (faster on Apple Silicon Neural Engine, slower on Pascal-era CUDA).
- HEIC images must be converted to JPEG/PNG before sending to any model — see
  [#33](https://github.com/beckandwhite/VisionKB/issues/33).

---

## What is a "token" for an image?

For text, a token is a short word fragment. For images the concept is different
— models do not tokenise pixels the way they tokenise text.

The image is resized and split into fixed-size **patches** (typically 14×14 or
16×16 pixels). Each patch is fed through a **vision encoder** (usually a ViT —
Vision Transformer) which produces one embedding vector per patch. Those vectors
are what the language model sees — they play the role tokens do in text.

Example: a 336×336 image with 14×14 patches →
`(336 ÷ 14)² = 576 patches` → **576 visual "tokens"** injected into the
language model's context.

**Why this affects throughput:**
A high-resolution screenshot (e.g. `max_dim = 2560`) produces far more patches
than a small image. More patches = longer context = slower generation. This is
why vision models are slower per image than pure text tasks, and why the tok/s
figures in the tables above are slightly misleading for vision workloads — the
patch-encoding step and the inflated context length are both overhead on top of
the quoted text-generation throughput.

**The practical shortcut:** when someone quotes "tok/s" for a vision model,
they mean text output tokens only. The patch embeddings are a fixed one-time
cost per image and are not counted in that figure.
