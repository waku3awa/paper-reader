import os
import base64
import cv2
import numpy as np
import arxiv
from openai import OpenAI
import google.generativeai as genai
import gradio as gr
from io import BytesIO
import pdf2image
import tempfile
import layoutparser as lp
from datetime import datetime
from pdfminer.high_level import extract_text
from dotenv import load_dotenv
import time
import re
from llm_utils import get_token_len, get_text_before_word

truncate_len = 30000
load_dotenv()

# 環境変数からAPIキーを取得
google_api_key = os.environ.get("GOOGLEAI_API_KEY")
if not google_api_key:
    raise ValueError("GOOGLEAI_API_KEY environment variable not set")
else:
    genai.configure(api_key=google_api_key)

# OpenAIクライアントの初期化
ai_client = genai.GenerativeModel(
    # model_name="gemini-1.5-pro-latest",
    model_name='gemini-1.5-flash-latest',
    system_instruction="あなたは優秀な研究者です"
)

local_client = OpenAI(base_url="http://172.19.128.1:1234/v1", api_key="lm-studio")


def download_paper(arxiv_url: str, save_dir: str) -> str:
    """
    arXivから論文をダウンロードする関数

    Args:
        arxiv_url (str): ダウンロードする論文のarXivのURL
        save_dir (str): 論文を保存するディレクトリのパス

    Returns:
        str: ダウンロードした論文のPDFファイルのパス
    """
    paper_id = arxiv_url.split("/")[-1]
    paper = next(arxiv.Client().results(arxiv.Search(id_list=[paper_id])))

    os.makedirs(save_dir, exist_ok=True)
    filename = f"{paper_id}.pdf"
    pdf_path = os.path.join(save_dir, filename)
    paper.download_pdf(dirpath=save_dir, filename=filename)

    return pdf_path, paper.title


def extract_figures_and_tables(pdf_path: str, save_dir: str) -> list:
    """
    PDFから図表を抽出する関数

    Args:
        pdf_path (str): 図表を抽出するPDFファイルのパス
        save_dir (str): 抽出した画像を保存するディレクトリのパス

    Returns:
        list: 抽出した図表の情報を格納したリスト
    """
    model = lp.Detectron2LayoutModel(
        "lp://PubLayNet/faster_rcnn_R_50_FPN_3x/config",
        extra_config=["MODEL.ROI_HEADS.SCORE_THRESH_TEST", 0.8],
        label_map={0: "Text", 1: "Title", 2: "List", 3: "Table", 4: "Figure"},
    )

    images = pdf2image.convert_from_path(pdf_path)

    figure_and_table_data = []
    os.makedirs(save_dir, exist_ok=True)

    for i, image in enumerate(images):
        image_np = np.array(image)
        image_np = cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR)
        layout = model.detect(image_np)

        for j, block in enumerate(layout):
            if block.type in ["Table", "Figure"]:
                segment_image = block.pad(left=5, right=10, top=15, bottom=5).crop_image(
                    image_np
                )
                image_path = os.path.join(save_dir, f"page_{i}_block_{j}.jpg")
                cv2.imwrite(
                    image_path, segment_image, [int(cv2.IMWRITE_JPEG_QUALITY), 95]
                )
                with open(image_path, "rb") as f:
                    base64_image = base64.b64encode(f.read()).decode("utf-8")
                figure_and_table_data.append(
                    {"path": image_path, "base64": base64_image, "type": block.type}
                )

    return figure_and_table_data


def extract_formulas(pdf_path: str, save_dir: str) -> list:
    """
    PDFから数式を抽出する関数

    Args:
        pdf_path (str): 数式を抽出するPDFファイルのパス
        save_dir (str): 抽出した画像を保存するディレクトリのパス

    Returns:
        list: 抽出した数式の情報を格納したリスト
    """
    model = lp.Detectron2LayoutModel(
        "lp://MFD/faster_rcnn_R_50_FPN_3x/config",
        extra_config=["MODEL.ROI_HEADS.SCORE_THRESH_TEST", 0.8],
        label_map={1: "Equation"},
    )

    images = pdf2image.convert_from_path(pdf_path)

    figure_and_table_data = []
    os.makedirs(save_dir, exist_ok=True)

    for i, image in enumerate(images):
        image_np = np.array(image)
        image_np = cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR)
        layout = model.detect(image_np)

        for j, block in enumerate(layout):
            if block.type in ["Equation"]:
                segment_image = block.pad(left=5, right=5, top=5, bottom=5).crop_image(
                    image_np
                )
                image_path = os.path.join(save_dir, f"page_{i}_block_{j}.jpg")
                cv2.imwrite(
                    image_path, segment_image, [int(cv2.IMWRITE_JPEG_QUALITY), 95]
                )
                with open(image_path, "rb") as f:
                    base64_image = base64.b64encode(f.read()).decode("utf-8")
                figure_and_table_data.append(
                    {"path": image_path, "base64": base64_image, "type": block.type}
                )

    return figure_and_table_data


def pdf_to_base64(pdf_path: str) -> list:
    """
    PDFをbase64エンコードされた画像のリストに変換する関数

    Args:
        pdf_path (str): 変換するPDFファイルのパス

    Returns:
        list: base64エンコードされた画像のリスト
    """
    images = pdf2image.convert_from_path(pdf_path)

    base64_images = []

    for image in images:
        buffered = BytesIO()
        image.save(buffered, format="jpeg")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        base64_images.append(img_str)

    return base64_images


def generate_image_explanation(image: str, pdf_text: str) -> str:
    """
    画像の説明を生成する関数

    Args:
        image (str): base64エンコードされた画像
        pdf_text (str): 論文から抽出したテキスト

    Returns:
        str: 生成された画像の説明
    """
    start = datetime.now()
    print(f'generate_image_explanation {start}')
    sample_file = genai.upload_file(path=image,
                           display_name="Figure of paper")
    # アップロード完了をチェック
    # `upload_file` は非同期的に実行されるため、完了を待たないと次の処理でエラーが発生してしまう
    while sample_file.state.name == "PROCESSING":
      print("Waiting for processed.")
      time.sleep(5)
      sample_file = genai.get_file(sample_file.name)
    response = ai_client.generate_content([
                f"論文から抽出したテキスト情報は以下です:\n{pdf_text}\n\n提供された論文の画像の示す意味を説明してください。",
                "これは論文から抽出した画像です",
                sample_file,
                "説明はMarkdown形式かつ日本語で記述してください。",
                ])
    genai.delete_file(
        sample_file
    )
    print(response)
    return response.text


def generate_formula_explanation(image: str, pdf_text: str) -> str:
    """
    数式の説明を生成する関数

    Args:
        image (str): base64エンコードされた数式の画像
        pdf_text (str): 論文から抽出したテキスト

    Returns:
        str: 生成された数式の説明
    """
    start = datetime.now()
    print(f'generate_formula_explanation {start}')
    sample_file = genai.upload_file(path=image,
                           display_name="Formula of paper")
    # アップロード完了をチェック
    # `upload_file` は非同期的に実行されるため、完了を待たないと次の処理でエラーが発生してしまう
    while sample_file.state.name == "PROCESSING":
      print("Waiting for processed.")
      time.sleep(5)
      sample_file = genai.get_file(sample_file.name)
    response = ai_client.generate_content([
                f"あなたは優秀な研究者です。論文から抽出したテキスト情報は以下です:\n{pdf_text}\n\n提供された論文の数式部分の画像を提供するので、この数式の解説を行ってください",
                "これは論文から抽出した画像です",
                sample_file,
                "数式はmarkdown内で使え、LaTeX の記法を用いて数式を記述することができるmathjaxを用い$$で囲んでください。解説はMarkdown形式かつ日本語で記述してください。Markdownは```で囲まないでください",
        ])
    genai.delete_file(
        sample_file
    )
    print(response)
    return response.text


def generate_paper_summary_ochiai(images: list, arxiv_url: str) -> str:
    """
    落合メソッドで論文の要約を生成する関数

    Args:
        images (list): base64エンコードされた論文の画像のリスト
        arxiv_url (str): 論文のarXivのURL

    Returns:
        str: 生成された論文の要約
    """
    token_count = ai_client.count_tokens(images)
    print(f'{token_count}')
    start = datetime.now()
    print(f'generate_paper_summary_ochiai {start}')
    response = ai_client.generate_content([
        """あなたは優秀な研究者です。提供された論文の画像を元に以下のフォーマットに従って論文の解説を行ってください。

# {論文タイトル}

date: {YYYY-MM-DD}
categories: {論文のカテゴリ}

## 1. どんなもの？
## 2. 先行研究と比べてどこがすごいの？
## 3. 技術や手法の"キモ"はどこにある？
## 4. どうやって有効だと検証した？
## 5. 議論はあるか？
## 6. 次に読むべき論文はあるか？
## 7. 想定される質問と回答
## 論文情報・リンク
- [著者，"タイトル，" ジャーナル名 voluem no.，ページ，年](論文リンク)
"""

f"論文URL: {arxiv_url}",
                    *images,
                    "論文の解説はMarkdown形式かつ日本語で記述してください。",
        ])
    end = datetime.now()
    print('Time:', end-start)
    print('ochiai_withimage:')
    print(response)
    return response.text

def generate_paper_summary_ochiai_text(pdf_text: str, arxiv_url: str) -> str:
    """
    落合メソッドで論文の要約を生成する関数

    Args:
        pdf_text (str): 論文から抽出したテキスト
        arxiv_url (str): 論文のarXivのURL

    Returns:
        str: 生成された論文の要約
    """
    # Referencesの前までを取り出す
    pdf_text = get_text_before_word(pdf_text, 'References')
    token_count = ai_client.count_tokens(pdf_text)
    pdf_text = pdf_text[:truncate_len]
    text_len = get_token_len(pdf_text)
    token_count_trunc = ai_client.count_tokens(pdf_text)
    start = datetime.now()
    print(f'generate_paper_summary_ochiai_text {start}')
    print(f'{token_count} -> {token_count_trunc}')
    response = ai_client.generate_content([
        """あなたは優秀な研究者です。提供された論文の画像を元に以下のフォーマットに従って論文の解説を行ってください。

# {論文タイトル}

date: {YYYY-MM-DD}
categories: {論文のカテゴリ}

## 1. どんなもの？
## 2. 先行研究と比べてどこがすごいの？
## 3. 技術や手法の"キモ"はどこにある？
## 4. どうやって有効だと検証した？
## 5. 議論はあるか？
## 6. 次に読むべき論文はあるか？
## 7. 想定される質問と回答
## 論文情報・リンク
- [著者，"タイトル，" ジャーナル名 voluem no.，ページ，年](論文リンク)
"""

f"論文URL: {arxiv_url}",

f"""論文URL: {arxiv_url}
以下が論文の内容です

---
{pdf_text}
---
論文の解説はMarkdown形式かつ日本語で記述してください。""",
        ])
    end = datetime.now()
    print('Time:', end-start)
    print('ochiai_withtext:')
    print(response)
    return response.text


def generate_paper_summary_ochiai_text_formula(pdf_text: str, images: list, arxiv_url: str) -> str:
    """
    落合メソッドで論文の要約を生成する関数

    Args:
        pdf_text (str): 論文から抽出したテキスト
        arxiv_url (str): 論文のarXivのURL

    Returns:
        str: 生成された論文の要約
    """
    # Referencesの前までを取り出す
    pdf_text = get_text_before_word(pdf_text, 'References')
    token_count = ai_client.count_tokens(pdf_text)
    pdf_text = pdf_text[:truncate_len]
    text_len = get_token_len(pdf_text)
    token_count_trunc = ai_client.count_tokens(pdf_text)
    start = datetime.now()
    print(f'generate_paper_summary_ochiai_text {start}')
    print(f'{token_count} -> {token_count_trunc}')

    # 画像の準備 ---
    sample_files = []
    file_names = []
    for image_data in images:
        # {"path": image_path, "base64": base64_image, "type": block.type}
        sample_file = genai.upload_file(path=image_data['path'],
                           display_name=os.path.basename(image_data['path']))
    # アップロード完了をチェック
    # `upload_file` は非同期的に実行されるため、完了を待たないと次の処理でエラーが発生してしまう
    while sample_file.state.name == "PROCESSING":
        print("Waiting for processed.")
        time.sleep(5)
    sample_files.append(genai.get_file(sample_file.name))
    file_names.append(sample_file.name)
    # ---

    response = ai_client.generate_content([
        """あなたは優秀な研究者です。提供された論文の画像を元に以下のフォーマットに従って論文の解説を行ってください。

# {論文タイトル}

date: {YYYY-MM-DD}
categories: {論文のカテゴリ}

## 1. どんなもの？
## 2. 先行研究と比べてどこがすごいの？
## 3. 技術や手法の"キモ"はどこにある？
## 4. どうやって有効だと検証した？
## 5. 議論はあるか？
## 6. 次に読むべき論文はあるか？
## 7. 想定される質問と回答
## 論文情報・リンク
- [著者，"タイトル，" ジャーナル名 voluem no.，ページ，年](論文リンク)
## キービジュアル
{画像名}
"""

f"論文URL: {arxiv_url}",

f"""論文URL: {arxiv_url}
以下が論文の内容です。

---
{pdf_text}
---
以下は論文から抽出した図、表、数式の画像です。必要であれば画像の情報を使って解説をしてください。また、この論文のキービジュアルを1枚選択し画像名を記入してください。""",
*[f'画像名: {name}, 画像: {file}' for file, name in zip(sample_files, file_names)],
"""数式はmarkdown内で使え、LaTeX の記法を用いて数式を記述することができるmathjaxを用い$$で囲んでください。解説はMarkdown形式かつ日本語で記述してください。Markdownは```で囲まないでください"

論文の解説はMarkdown形式かつ日本語で記述してください。""",
        ])
    end = datetime.now()
    print('Time:', end-start)
    genai.delete_file(sample_file)
    print('ochiai_withtext:')
    print(response)
    return response.text


def generate_paper_summary_ochiai_text_local(pdf_text: str, arxiv_url: str) -> str:
    """
    落合メソッドで論文の要約を生成する関数

    Args:
        pdf_text (str): 論文から抽出したテキスト
        arxiv_url (str): 論文のarXivのURL

    Returns:
        str: 生成された論文の要約
    """
    # Referencesの前までを取り出す
    pdf_text = get_text_before_word(pdf_text, 'References')
    txet_len = get_token_len(pdf_text)
    print(f'{txet_len = }')
    pdf_text = pdf_text[:10000]
    txet_len = get_token_len(pdf_text)
    print(f'{txet_len = }')
    start = datetime.now()
    print(f'generate_paper_summary_ochiai_text_local {start}')
    # print(f'{token_count} -> {token_count_trunc}')
    response = local_client.chat.completions.create(
        model="Mistral/mixtral-8x7b-instruct-v0.1",
        messages=[
                    {"role": "system", "content": """あなたは優秀な研究者です。提供された論文の画像を元に以下のフォーマットに従って論文の解説を行ってください。

# {論文タイトル}

date: {YYYY-MM-DD}
categories: {論文のカテゴリ}

## 1. どんなもの？
## 2. 先行研究と比べてどこがすごいの？
## 3. 技術や手法の"キモ"はどこにある？
## 4. どうやって有効だと検証した？
## 5. 議論はあるか？
## 6. 次に読むべき論文はあるか？
## 7. 想定される質問と回答
## 論文情報・リンク
- [著者，"タイトル，" ジャーナル名 voluem no.，ページ，年](論文リンク)
"""

f"論文URL: {arxiv_url}"""},
                    {"role": "user", "content": f"""論文URL: {arxiv_url}
以下が論文の内容です

---
{pdf_text}
---
論文の解説はMarkdown形式かつ日本語で記述してください。"""}
                ],
        temperature=0.7,
    )
    end = datetime.now()
    print('Time:', end-start)
    print('ochiai_withtext:')
    print(response.choices[0].message.content)
    return response.choices[0].message.content


def paper_reader(arxiv_url: str, pdf_file, processing_mode: str, processing_mode_body: str, ) -> tuple:
    """
    論文を読み、要約と説明を生成する関数

    Args:
        arxiv_url (str): 論文のarXivのURL
        processing_mode (str): 処理モード（"all", "formula_only", "none"のいずれか）

    Returns:
        tuple: 生成された論文の要約、数式・図表の説明、画像の説明のリスト
    """
    formatted_date = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_dir_name = os.path.join(tempfile.mkdtemp(), formatted_date)

    if pdf_file:
        pdf_path = pdf_file
        pdf_name = pdf_path.split('/')[-1].split('.')[0]
    else:
        pdf_path, pdf_name = download_paper(arxiv_url, save_dir_name)

    print(f'Load PDF, {pdf_path = }')

    if processing_mode == "all" or processing_mode == "text_formula":
        print('Extract images start')
        formula_data = extract_formulas(pdf_path, save_dir_name)
    else:
        formula_data = []
    # 図表は解説対象になくても添えるため抽出する
    figures_and_tables_data = extract_figures_and_tables(pdf_path, save_dir_name)

    print(f'Processing body start, {processing_mode_body = }')

    processing_body, llm_client = processing_mode_body.split('-')

    if 'body_text' == processing_body:
        pdf_text = extract_text(pdf_path)
    elif 'body_image' == processing_body:
        images = pdf_to_base64(pdf_path)
    else:
        pass

    if 'gemini' == llm_client and 'body_text' == processing_body:
        # テキスト抽出版 geminiはテキスト版じゃないと制限引っかかる
        paper_summary_ochiai = generate_paper_summary_ochiai_text_formula(pdf_text, figures_and_tables_data + formula_data, arxiv_url)
        # paper_summary_ochiai = generate_paper_summary_ochiai_text(pdf_text, arxiv_url)
    elif 'gemini' == llm_client and 'body_image' == processing_body:
        paper_summary_ochiai = generate_paper_summary_ochiai(images, arxiv_url) # 画像版
    else:
        paper_summary_ochiai = generate_paper_summary_ochiai_text_local(pdf_text, arxiv_url)
    print(f'Processing body end, {processing_mode_body = }')

    gallery_data = []
    if processing_mode == "text_only":
        explaination_text = ""
    else:
        print('Processing formulas start')
        explaination_text = "# 数式の説明\n\n"
        for i, data in enumerate(formula_data):
            # explanation = generate_formula_explanation(data["base64"], pdf_text)
            explanation = generate_formula_explanation(data["path"], pdf_text)
            gallery_data.append([data["path"], explanation])
            explaination_text += f"## 数式画像{i}\n\n![](data:image/jpg;base64,{data['base64']})\n\n{explanation}\n\n"
        print('Processing formulas end')
        if processing_mode != "formula_only":
            print('Processing figures_and_tables start')
            explaination_text += "# 図表の説明\n\n"
            for i, data in enumerate(figures_and_tables_data):
                # explanation = generate_image_explanation(data["base64"], pdf_text)
                explanation = generate_image_explanation(data["path"], pdf_text)
                gallery_data.append([data["path"], explanation])
                explaination_text += f"## 画像{i}\n\n![](data:image/jpg;base64,{data['base64']})\n\n{explanation}\n\n"
            print('Processing figures_and_tables end')

    with open(f'output/summary_{pdf_name}.md', 'w', encoding='utf-8') as f:
        f.write(paper_summary_ochiai)
    if explaination_text:
        with open(f'output/explaination_{pdf_name}.md', 'w', encoding='utf-8') as f:
            f.write(explaination_text)

    return pdf_file, paper_summary_ochiai, explaination_text, gallery_data, pdf_text[:truncate_len]


# Blocksでappを定義
with gr.Blocks() as app:
    title="論文の解説を落合メソッドで生成するアプリ"
    inputs = [
        gr.Textbox(
            label="論文URL (arXiv)", placeholder="例: https://arxiv.org/abs/2405.16153"
        ),
        gr.File(
            file_count="single", file_types=[".pdf"], height=30, label="PDF(こちらが優先)", type='filepath',
        ),
        gr.Radio(
            choices=[
                ("テキスト、数式、図表の解説を行う", "all"),
                ("テキスト、数式の解説を行う", "text_formula"),
                ("テキストの解説のみ行う", "text_only"),
            ],
            label="処理方式",
            value="all",
        ),

        gr.Radio(
            choices=[
                ("本文の解説をテキストでGeminiで行う", "body_text-gemini"),
                ("本文の解説をテキストでローカルLLMで行う", "body_text-local"),
                ("本文の解説を画像でGeminiで行う", "body_image-gemini"),
            ],
            label="本文の処理方式",
            value="body_text-gemini",
        ),
    ]
    btn = gr.Button("クリックしてね!")
    outputs = [
        gr.Textbox(label="pdf", show_label=True, lines=2, max_lines=2, interactive=False, container=True),
        gr.Markdown(label="落合メソッドでの解説", show_label=True),
        gr.Markdown(label="数式, 図表の解説", show_label=True),
        gr.Gallery(label="画像説明", show_label=True, elem_id="gallery"),
        gr.Markdown(label="落合メソッドでの解説", show_label=True),
    ]
    # イベントを定義
    btn.click(fn=paper_reader, inputs=inputs, outputs=outputs)

if __name__ == "__main__":
    app.launch(share=True)
