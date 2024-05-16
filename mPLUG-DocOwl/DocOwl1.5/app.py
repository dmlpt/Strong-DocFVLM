import argparse
import datetime
import json
import os
import time

import gradio as gr
import requests

from mplug_docowl.conversation import (default_conversation, conv_templates,
                                   SeparatorStyle)
from mplug_docowl.constants import LOGDIR
from mplug_docowl.utils import (build_logger, server_error_msg,
    violates_moderation, moderation_msg)
from model_worker import ModelWorker
import hashlib
from icecream import ic


logger = build_logger("gradio_web_server_local", "gradio_web_server_local.log")

headers = {"User-Agent": "mPLUG-DocOwl1.5 Client"}

no_change_btn = gr.Button.update()
enable_btn = gr.Button.update(interactive=True)
disable_btn = gr.Button.update(interactive=False)

def get_conv_log_filename():
    t = datetime.datetime.now()
    name = os.path.join(LOGDIR, f"{t.year}-{t.month:02d}-{t.day:02d}-conv.json")
    return name

get_window_url_params = """
function() {
    const params = new URLSearchParams(window.location.search);
    url_params = Object.fromEntries(params);
    console.log(url_params);
    return url_params;
    }
"""


def load_demo(url_params, request: gr.Request):
    logger.info(f"load_demo. ip: {request.client.host}. params: {url_params}")
    state = default_conversation.copy()
    return state


def vote_last_response(state, vote_type, request: gr.Request):
    with open(get_conv_log_filename(), "a") as fout:
        data = {
            "tstamp": round(time.time(), 4),
            "type": vote_type,
            "state": state.dict(),
            "ip": request.client.host,
        }
        fout.write(json.dumps(data) + "\n")


def upvote_last_response(state, request: gr.Request):
    logger.info(f"upvote. ip: {request.client.host}")
    vote_last_response(state, "upvote", request)
    return ("",) + (disable_btn,) * 3


def downvote_last_response(state, request: gr.Request):
    logger.info(f"downvote. ip: {request.client.host}")
    vote_last_response(state, "downvote", request)
    return ("",) + (disable_btn,) * 3


def flag_last_response(state, request: gr.Request):
    logger.info(f"flag. ip: {request.client.host}")
    vote_last_response(state, "flag", request)
    return ("",) + (disable_btn,) * 3


def regenerate(state, image_process_mode, request: gr.Request):
    logger.info(f"regenerate. ip: {request.client.host}")
    state.messages[-1][-1] = None
    prev_human_msg = state.messages[-2]
    if type(prev_human_msg[1]) in (tuple, list):
        prev_human_msg[1] = (*prev_human_msg[1][:2], image_process_mode)
    state.skip_next = False
    return (state, state.to_gradio_chatbot(), "", None) + (disable_btn,) * 5


def clear_history(request: gr.Request):
    logger.info(f"clear_history. ip: {request.client.host}")
    state = default_conversation.copy()
    return (state, state.to_gradio_chatbot(), "", None) + (disable_btn,) * 5


def add_text(state, text, image, image_process_mode, request: gr.Request):
    logger.info(f"add_text. ip: {request.client.host}. len: {len(text)}")
    if len(text) <= 0 and image is None:
        state.skip_next = True
        return (state, state.to_gradio_chatbot(), "", None) + (no_change_btn,) * 5
    if args.moderate:
        flagged = violates_moderation(text)
        if flagged:
            state.skip_next = True
            return (state, state.to_gradio_chatbot(), moderation_msg, None) + (
                no_change_btn,) * 5

    text = text[:3584]  # Hard cut-off
    if image is not None:
        text = text[:3500]  # Hard cut-off for images
        if '<|image|>' not in text:
            text = '<|image|>' + text
        text = (text, image, image_process_mode)
        if len(state.get_images(return_pil=True)) > 0:
            state = default_conversation.copy()
    state.append_message(state.roles[0], text)
    state.append_message(state.roles[1], None)
    state.skip_next = False
    return (state, state.to_gradio_chatbot(), "", None) + (disable_btn,) * 5


def http_bot(state, temperature, top_p, max_new_tokens, request: gr.Request):
    logger.info(f"http_bot. ip: {request.client.host}")
    start_tstamp = time.time()

    if state.skip_next:
        # This generate call is skipped due to invalid inputs
        yield (state, state.to_gradio_chatbot()) + (no_change_btn,) * 5
        return

    if len(state.messages) == state.offset + 2:
        # First round of conversation
        template_name = "mplug_owl2"
        new_state = conv_templates[template_name].copy()
        new_state.append_message(new_state.roles[0], state.messages[-2][1])
        new_state.append_message(new_state.roles[1], None)
        state = new_state

    # Construct prompt
    prompt = state.get_prompt()

    all_images = state.get_images(return_pil=True)
    # debug
    """for image in all_images:
        ic(image.size)"""
    all_image_hash = [hashlib.md5(image.tobytes()).hexdigest() for image in all_images]
    for image, hash in zip(all_images, all_image_hash):
        t = datetime.datetime.now()
        filename = os.path.join(LOGDIR, "serve_images", f"{t.year}-{t.month:02d}-{t.day:02d}", f"{hash}.jpg")
        if not os.path.isfile(filename):
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            image.save(filename)

    # Make requests
    pload = {
        "prompt": prompt,
        "temperature": float(temperature),
        "top_p": float(top_p),
        "max_new_tokens": min(int(max_new_tokens), 2048),
        "stop": state.sep if state.sep_style in [SeparatorStyle.SINGLE, SeparatorStyle.MPT] else state.sep2,
        "images": f'List of {len(state.get_images())} images: {all_image_hash}',
    }

    logger.info(f"==== request ====\n{pload}")

    pload['images'] = state.get_images()

    state.messages[-1][-1] = "▌"
    yield (state, state.to_gradio_chatbot()) + (disable_btn,) * 5

    try:
        # Stream output
        # response = requests.post(worker_addr + "/worker_generate_stream",
        #     headers=headers, json=pload, stream=True, timeout=10)
        # for chunk in response.iter_lines(decode_unicode=False, delimiter=b"\0"):
        response = model.generate_stream_gate(pload)
        # print('response:', response)
        for chunk in response:
            if chunk:
                print('chunk:', chunk.decode())
                data = json.loads(chunk.decode())
                if data["error_code"] == 0:
                    output = data["text"][len(prompt):].strip()
                    state.messages[-1][-1] = output + "▌"
                    yield (state, state.to_gradio_chatbot()) + (disable_btn,) * 5
                else:
                    output = data["text"] + f" (error_code: {data['error_code']})"
                    state.messages[-1][-1] = output
                    yield (state, state.to_gradio_chatbot()) + (disable_btn, disable_btn, disable_btn, enable_btn, enable_btn)
                    return
                time.sleep(0.03)
    except requests.exceptions.RequestException as e:
        state.messages[-1][-1] = server_error_msg
        yield (state, state.to_gradio_chatbot()) + (disable_btn, disable_btn, disable_btn, enable_btn, enable_btn)
        return

    state.messages[-1][-1] = state.messages[-1][-1][:-1]
    yield (state, state.to_gradio_chatbot()) + (enable_btn,) * 5

    finish_tstamp = time.time()
    logger.info(f"{output}")

    with open(get_conv_log_filename(), "a") as fout:
        data = {
            "tstamp": round(finish_tstamp, 4),
            "type": "chat",
            "start": round(start_tstamp, 4),
            "finish": round(start_tstamp, 4),
            "state": state.dict(),
            "images": all_image_hash,
            "ip": request.client.host,
        }
        fout.write(json.dumps(data) + "\n")


title_markdown = ("""
<h1 align="center"><a href="https://github.com/X-PLUG/mPLUG-DocOwl"><img src="https://github.com/X-PLUG/mPLUG-DocOwl/raw/main/assets/mPLUG_new1.png", alt="mPLUG-DocOwl" border="0" style="margin: 0 auto; height: 200px;" /></a> </h1>

<h2 align="center"> mPLUG-DocOwl1.5: Unified Stucture Learning for OCR-free Document Understanding</h2>

<h5 align="center"> If you like our project, please give us a star ✨ on Github for latest update.  </h2>

<h5 align="center"> Note: This demo is temporarily only supported for English Document Understanding. The Chinese-and-English model is under development.</h2>

<h5 align="center"> 注意: 当前Demo只支持英文文档理解, 中英模型正在全力开发中。</h2>

<h5 align="center"> Note: If you want a detailed explanation, please remember to add a prompot "Give a detailed explanation." after the question.</h2>

<h5 align="center"> 注意: 如果你想要详细的推理解释, 请在问题后面加上“Give a detailed explanation.”。</h2>


<div align="center">
    <div style="display:flex; gap: 0.25rem;" align="center">
        <a href='https://github.com/X-PLUG/mPLUG-DocOwl'><img src='https://img.shields.io/badge/Github-Code-blue'></a>
        <a href="https://arxiv.org/abs/2403.12895"><img src="https://img.shields.io/badge/Arxiv-2403.12895-red"></a>
        <a href='https://github.com/X-PLUG/mPLUG-DocOwl/stargazers'><img src='https://img.shields.io/github/stars/X-PLUG/mPLUG-DocOwl.svg?style=social'></a>
    </div>
</div>

""")


tos_markdown = ("""
### Terms of use
By using this service, users are required to agree to the following terms:
The service is a research preview intended for non-commercial use only. It only provides limited safety measures and may generate offensive content. It must not be used for any illegal, harmful, violent, racist, or sexual purposes. The service may collect user dialogue data for future research.
Please click the "Flag" button if you get any inappropriate answer! We will collect those to keep improving our moderator.
For an optimal experience, please use desktop computers for this demo, as mobile devices may compromise its quality.
""")


learn_more_markdown = ("""
### License
The service is a research preview intended for non-commercial use only, subject to the model [License](https://github.com/facebookresearch/llama/blob/main/MODEL_CARD.md) of LLaMA, [Terms of Use](https://openai.com/policies/terms-of-use) of the data generated by OpenAI, and [Privacy Practices](https://chrome.google.com/webstore/detail/sharegpt-share-your-chatg/daiacboceoaocpibfodeljbdfacokfjb) of ShareGPT. Please contact us if you find any potential violation.
""")

block_css = """

#buttons button {
    min-width: min(120px,100%);
}

.bot {
  white-space: break-spaces;
}

"""

def build_demo(embed_mode):
    textbox = gr.Textbox(show_label=False, placeholder="Enter text and press ENTER", container=False)
    with gr.Blocks(title="mPLUG-DocOwl1.5", theme=gr.themes.Default(), css=block_css) as demo:
        state = gr.State()

        if not embed_mode:
            gr.Markdown(title_markdown)

        with gr.Row():
            with gr.Column(scale=3):
                imagebox = gr.Image(type="pil")
                image_process_mode = gr.Radio(
                    # ["Crop", "Resize", "Pad", "Default"],
                    [],
                    value="Default",
                    label="Preprocess for non-square image", visible=False)
                

                cur_dir = os.path.dirname(os.path.abspath(__file__))
                gr.Examples(examples=[
                    [f"{cur_dir}/examples/cvpr.png", "what is this schedule for? Give detailed explanation."],
                    [f"{cur_dir}/examples/fflw0023_1.png", "Parse texts in the image."],
                    [f"{cur_dir}/examples/col_type_46452.jpg", "Convert the table into Markdown format."],
                    [f"{cur_dir}/examples/col_type_177029.jpg", "What is unusual about this image? Provide detailed explanation."],
                    [f"{cur_dir}/examples/multi_col_60204.png", "Convert the illustration into Markdown language."],
                    [f"{cur_dir}/examples/Rebecca_(1939_poster)_Small.jpeg", "What is the name of the movie in the poster? Provide detailed explanation."],
                    [f"{cur_dir}/examples/extreme_ironing.jpg", "What is unusual about this image? Provide detailed explanation."],
                ], inputs=[imagebox, textbox])

                with gr.Accordion("Parameters", open=True) as parameter_row:
                    temperature = gr.Slider(minimum=0.0, maximum=1.0, value=1.0, step=0.1, interactive=True, label="Temperature",)
                    top_p = gr.Slider(minimum=0.0, maximum=1.0, value=0.7, step=0.1, interactive=True, label="Top P",)
                    max_output_tokens = gr.Slider(minimum=0, maximum=1024, value=512, step=64, interactive=True, label="Max output tokens",)

            with gr.Column(scale=8):
                chatbot = gr.Chatbot(elem_id="Chatbot", label="mPLUG-DocOwl1.5 Chatbot", height=600)
                with gr.Row():
                    with gr.Column(scale=8):
                        textbox.render()
                    with gr.Column(scale=1, min_width=50):
                        submit_btn = gr.Button(value="Send", variant="primary")
                with gr.Row(elem_id="buttons") as button_row:
                    upvote_btn = gr.Button(value="👍  Upvote", interactive=False)
                    downvote_btn = gr.Button(value="👎  Downvote", interactive=False)
                    flag_btn = gr.Button(value="⚠️  Flag", interactive=False)
                    #stop_btn = gr.Button(value="⏹️  Stop Generation", interactive=False)
                    regenerate_btn = gr.Button(value="🔄  Regenerate", interactive=False)
                    clear_btn = gr.Button(value="🗑️  Clear", interactive=False)

        if not embed_mode:
            gr.Markdown(tos_markdown)
            gr.Markdown(learn_more_markdown)
        url_params = gr.JSON(visible=False)

        # Register listeners
        btn_list = [upvote_btn, downvote_btn, flag_btn, regenerate_btn, clear_btn]
        upvote_btn.click(
            upvote_last_response,
            state,
            [textbox, upvote_btn, downvote_btn, flag_btn],
            queue=False
        )
        downvote_btn.click(
            downvote_last_response,
            state,
            [textbox, upvote_btn, downvote_btn, flag_btn],
            queue=False
        )
        flag_btn.click(
            flag_last_response,
            state,
            [textbox, upvote_btn, downvote_btn, flag_btn],
            queue=False
        )

        regenerate_btn.click(
            regenerate,
            [state, image_process_mode],
            [state, chatbot, textbox, imagebox] + btn_list,
            queue=False
        ).then(
            http_bot,
            [state, temperature, top_p, max_output_tokens],
            [state, chatbot] + btn_list
        )

        clear_btn.click(
            clear_history,
            None,
            [state, chatbot, textbox, imagebox] + btn_list,
            queue=False
        )

        textbox.submit(
            add_text,
            [state, textbox, imagebox, image_process_mode],
            [state, chatbot, textbox, imagebox] + btn_list,
            queue=False
        ).then(
            http_bot,
            [state, temperature, top_p, max_output_tokens],
            [state, chatbot] + btn_list
        )

        submit_btn.click(
            add_text,
            [state, textbox, imagebox, image_process_mode],
            [state, chatbot, textbox, imagebox] + btn_list,
            queue=False
        ).then(
            http_bot,
            [state, temperature, top_p, max_output_tokens],
            [state, chatbot] + btn_list
        )

        demo.load(
            load_demo,
            [url_params],
            state,
            _js=get_window_url_params,
            queue=False
        )

    return demo


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int)
    parser.add_argument("--concurrency-count", type=int, default=10)
    parser.add_argument("--model-list-mode", type=str, default="once",
        choices=["once", "reload"])
    parser.add_argument("--model-source", type=str, default="modelscope",
        choices=["local", "modelscope", "huggingface"])
    parser.add_argument("--model-version", type=str, default="Omni", 
        choices=['stage1', 'Chat','Omni'])
    parser.add_argument("--model-path", type=str, default="iic/DocOwl1___5-Omni")
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument("--load-8bit", action="store_true")
    parser.add_argument("--load-4bit", action="store_true")
    parser.add_argument("--moderate", action="store_true")
    parser.add_argument("--embed", action="store_true")
    args = parser.parse_args()
    logger.info(f"args: {args}")

    if args.model_source == 'modelscope':
        # download model from modelscope
        from modelscope.hub.snapshot_download import snapshot_download
        model_dir = snapshot_download('iic/DocOwl1.5-'+args.model_version, cache_dir='./')
        args.model_path = 'iic/DocOwl1___5-'+args.model_version
    elif args.model_source == 'huggingface':
        # download model from huggingface
        from huggingface_hub import snapshot_download
        model_dir = snapshot_download('mPLUG/DocOwl1.5-'+args.model_version, cache_dir='./')
        args.model_path = 'mPLUG/DocOwl1.5-'+args.model_version

    print(os.listdir('./'))

    model = ModelWorker(args.model_path, None, None, 
            resolution=448, 
            anchors='grid_9',
            add_global_img=True,
            load_8bit=args.load_8bit, 
            load_4bit=args.load_4bit, 
            device=args.device)

    logger.info(args)
    demo = build_demo(args.embed)
    demo.queue(
        concurrency_count=args.concurrency_count,
        api_open=False
    ).launch(
        server_name=args.host,
        server_port=args.port,
        share=False
    )
