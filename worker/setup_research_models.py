"""Explicit installation step. Never imported/executed by a research tool."""
import json
import sys
from pathlib import Path
from docling.utils.model_downloader import download_models

out=Path(sys.argv[1]).resolve()
download_models(output_dir=out,with_layout=True,with_tableformer=True,
    with_code_formula=False,with_picture_classifier=False,with_rapidocr=True,rapidocr_models=["onnxruntime:en"])
(out/'models-ready.json').write_text(json.dumps({'layout':True,'tableformer':True,'ocr':'RapidOCR English onnxruntime'})+'\n')
(out/"ocr-ready.json").write_text(json.dumps({"rapidocr":"en","backend":"onnxruntime"})+"\n")
