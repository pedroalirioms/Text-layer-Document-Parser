# 🧩 Config-Driven PDF Data Extraction Engine

## 📖 The Origin Story

This project was born out of a complex data accessibility challenge. I was vibe-coding an Electron App for a personal research project and I was in need of data available only through a consumer-grade PDF. As the files were meant for reading only, there wasn't any effort put into accessing the data itself.

Standard tools and OCR pipelines failed to reliably convert the data with the required precision for the project. Even a slight misspelling of some data point would impact the research.

## 🛠️ The Solution: Vibe-Coded Iterative Development

To solve this issue, I investigated the text layer of the PDFs to find some patterns. I was able to see that some words and text structures could be used as "anchors" to extract the data and later be organized in JSON format.

With some extensive vibe coding, as Gemini was failing to identify the patterns, a pipeline could finally be built.

For the first part of the research, about a hundred PDFs were successfully converted into JSON files. Each PDF had between 8-15 individual data groups, so I could have a little more than 1,000 data groups to analyze.

## 🔄 Evolution: Technical Demonstration

While the engine was initially built for a specific industry format, it has been refactored into a Modular, Schema-Agnostic Engine.

The original data was freely distributed, but nonetheless proprietary. So, in order to respect intellectual property, this repository includes a Dummy Configuration Profile. The code (which was AI-assisted) can be evaluated, but the app will only work with the specific config.json file.

## ⚙️ Other uses of the scripts

This engine was powerful enough to be adapted for extracting data from publicly available government files for a real-life court case.


## 🚀 Usage
Interactive Mode: Run streamlit run app.py to use the web interface.

CLI Mode: Use python app.py --input <folder> --config <file> for batch processing.
