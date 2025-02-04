#!/usr/bin/env python3

import datetime
import html
import os
import queue
import subprocess
import sys
import threading

import diffusers
import gradio as gr
import torch
import transformers
from transformers.pipelines import audio_utils


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = "generated"

THEMES = (
    "",
    "renaissance oil painting style.",
    "anime style.",
    "line art style.",
    "bright watercolor style.",
    "closeup portrait photo, dramatic.",
    "soviet propaganda poster style.",
    "minecraft block style.",
    "3d, low-poly game art, polygon mesh, jagged, blocky.",
    "retro comic book style.",
)

INJECTED_CSS = """
body {
    height: 100vh;
}
#result {
    /* Gross hack, someone who knows better CSS than me, please enlighten me. */
    height: 90vh;
    display: flex;
    flex-direction: column-reverse;
    flex: 1 1 auto;
    max-height: 100%;
    overflow: auto !important;
}
"""


# Whisper tends to hallucinate on background noise common uttering. We don't
# want to generate these. We also skip anything less than 5 letters.
SKIPPED_UTTERING = (
  "thank you",
  "you're",
)

# Global states.
prompt_queue = queue.Queue()
generated_queue = queue.Queue()
# All the generated items.
_generated_items = []
_generating = None

# That's what you get when you don't have access to Go channels.
_lock = threading.Lock()
# Currently selected theme.
_theme = THEMES[-1]
# Last registered prompt.
_prompt = ""
# Suffix to append, aside the theme, as specified by the user.
_suffix = ""
# Time to go.
_stop = False

def get_theme():
    with _lock:
        return _theme

def set_theme(theme):
    global _theme
    with _lock:
        _theme = theme

def get_prompt():
    with _lock:
        return _prompt

def set_prompt(prompt):
    global _prompt
    with _lock:
        _prompt = prompt

def get_suffix():
    with _lock:
        return _suffix

def set_suffix(suffix):
    global _suffix
    with _lock:
        _suffix = suffix

def get_stop():
    with _lock:
        return _stop

def set_stop():
    global _stop
    with _lock:
        _stop = True


def generate_image(diffusionPipeline, prompt: str):
    now = str(datetime.datetime.now().replace(microsecond=0)).replace(" ", "-").replace(":", "-")
    name = f"{OUT_DIR}/image{now}"
    while os.path.exists(name + ".png"):
      # In the unlikely case more than one image is generated in the same
      # second.
      name += "b"
    sys.stdout.write(f"\nGenerating {name}.png for prompt: {repr(prompt)}\n")
    generated_queue.put((prompt, None))
    results = diffusionPipeline(
        prompt=prompt,
        num_inference_steps=3,
        guidance_scale=0.0,
        num_images = 1
    )
    img = results.images[0]
    img.save(name + ".png")
    with open(name + ".txt", "w") as f:
        f.write(prompt)
    generated_queue.put((prompt, name + ".png"))
    return img


def thread_generate_image(diffusionPipeline):
    while not get_stop():
        prompt = prompt_queue.get()
        if prompt is None:
            return
        suffix = get_suffix()
        if suffix:
            prompt += ", " + suffix
        theme = get_theme()
        if theme:
            prompt += ", " + theme
        img = generate_image(diffusionPipeline, prompt)


_microphone = None
def get_directshow_microphone():
    """Returns the first directshow microphone on Windows."""
    global _microphone
    if not _microphone:
        p = subprocess.Popen(
            ["ffmpeg", "-list_devices", "true", "-hide_banner", "-f", "dshow", "-i", "dummy"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = p.communicate()[1].splitlines()
        # The output depends on the ffmpeg version.
        while not out[0].endswith(b" DirectShow audio devices"):
                out = out[1:]
        _microphone = out[1].decode("ascii").split("]", 2)[1].strip()[1:-1]
        print(f"Using microphone: {_microphone}")
    return "audio=" + _microphone


def thread_transcribe(transcriber, chunk_length_s=5.0, stream_chunk_s=1.0):
    sys.stdout.write("Start talking....\n")
    while not get_stop():
        mic = audio_utils.ffmpeg_microphone_live(
            sampling_rate=transcriber.feature_extractor.sampling_rate,
            chunk_length_s=chunk_length_s,
            stream_chunk_s=stream_chunk_s,
        )
        text = ""
        for item in transcriber(mic, generate_kwargs={"max_new_tokens": 128}):
            if get_stop():
                return
            sys.stdout.write("\r\033[K" + item["text"])
            t = item["text"].strip()
            set_prompt(t)
            # When whisper determines the user is 'done', check if it's more
            # than 4 letters and not a mumbling.
            t = t.strip(",. ")
            if not item["partial"][0] and len(t) >= 5 and t.lower() not in SKIPPED_UTTERING:
                text = t
                break
        if text:
            prompt_queue.put(text)


def regen_result():
    """Runs every 0.5s to regenerate the output pane."""
    global _generating
    while True:
      try:
          txt, img = generated_queue.get_nowait()
          if not img:
              _generating = txt
          else:
              _generated_items.append((txt, img))
          if _generating == txt and img:
              _generating = None
      except queue.Empty:
          break
    out = "<br>\n".join(
        html.escape(txt) + "<img src=\"/file=" + html.escape(img) + "\" />"
        for txt, img in _generated_items)
    if _generating:
        out += "<br>\nGenerating " + html.escape(_generating)
    return get_prompt(), out


def get_whisper(device, torch_dtype):
    model_id = "distil-whisper/distil-medium.en"
    model = transformers.AutoModelForSpeechSeq2Seq.from_pretrained(
        model_id, torch_dtype=torch_dtype, low_cpu_mem_usage=False, use_safetensors=True
    )
    model.to(device)
    processor = transformers.AutoProcessor.from_pretrained(model_id)
    return transformers.pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        max_new_tokens=128,
        torch_dtype=torch_dtype,
        device=device,
    )


def get_sdxl_turbo(device):
    """Returns sdxl-turbo, which requires a bit more than 14GiB of VRAM."""
    pipe = diffusers.DiffusionPipeline.from_pretrained("stabilityai/sdxl-turbo").to(device)
    if sys.platform != "win32" and device != "mps":
        pipe.unet = torch.compile(pipe.unet, mode="reduce-overhead", fullgraph=True)
    pipe.upcast_vae()
    pipe.set_progress_bar_config(disable=True)
    return pipe


def get_ssd_lora(device):
    pipe = diffusers.DiffusionPipeline.from_pretrained("segmind/SSD-1B")
    pipe.load_lora_weights("latent-consistency/lcm-lora-ssd-1b")
    pipe.scheduler = diffusers.LCMScheduler.from_config(pipe.scheduler.config)
    if sys.platform != "win32" and device != "mps":
        pipe.unet = torch.compile(pipe.unet, mode="reduce-overhead", fullgraph=True)
    if device == "mps":
        pipe.to(device="mps", dtype=torch.float16)
    if device.startswith("cuda"):
        torch.backends.cuda.matmul.allow_tf32 = True
        pipe.to(device=device, dtype=torch.float16)
    pipe.set_progress_bar_config(disable=True)
    return pipe


def main():
    os.chdir(BASE_DIR)
    if not os.path.exists(OUT_DIR):
        os.mkdir(OUT_DIR)
    if sys.platform == "win32" or False:
        # Workaround a broken logic. I think this only happens on older ffmpeg
        # binaries, as I got different results on different computers.
        audio_utils._get_microphone_name = get_directshow_microphone

    # Determine the device acceleration type.
    device = "cpu"
    torch_dtype = torch.float32
    if torch.cuda.is_available():
        device = "cuda:0"
        torch_dtype = torch.float16
    elif torch.backends.mps.is_available():
        device = "mps"
        torch_dtype = torch.float16
    print(f"- Using device: {device}")

    print("- Loading whisper")
    transcriber = get_whisper(device, torch_dtype)
    # Run on CPU so the generation can run on the GPU.
    #transcriber = get_whisper("cpu", torch.float32)

    print("- Loading sdxl-turbo")
    diffusionPipeline = get_sdxl_turbo(device)
    #diffusionPipeline = get_ssd_lora(device)

    gr.utils.launch_counter = lambda: None
    with gr.Blocks(analytics_enabled=False, css=INJECTED_CSS) as ui:
        with gr.Row():
            with gr.Column():
                with gr.Row():
                    r = gr.Radio(choices=THEMES, value=get_theme(), label="Prompt theme")
                    r.select(fn=set_theme, inputs=r)
                with gr.Row():
                    with gr.Column():
                        suffix = gr.Textbox(label="Custom prompt")
                        suffix.input(set_suffix, inputs=suffix)
                with gr.Row():
                    with gr.Column():
                        heard = gr.Textbox(label="What I hear", interactive=False)
            with gr.Column():
                html = gr.HTML(value="", elem_id="result")
        ui.load(fn=regen_result, outputs=[heard, html], every=0.5)
    print("- Finished loading!")

    # Start the threads.
    threads = (
        threading.Thread(target=thread_generate_image, args=(diffusionPipeline,), daemon=True),
        threading.Thread(target=thread_transcribe, args=(transcriber,), daemon=True),
    )
    for t in threads:
        t.start()
    ui.launch(quiet=True, share=False, allowed_paths=[os.path.join(BASE_DIR, OUT_DIR)])
    # This doesn't work most of the time because the network server fails to
    # shut down. Shrug.
    print("Stopping...")
    set_stop()
    prompt_queue.put(None)
    for t in threads:
        t.join()


if __name__ == "__main__":
    sys.exit(main())
