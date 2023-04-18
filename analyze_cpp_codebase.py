import os
import sys
import json
import time
import re
from io import StringIO
import tempfile
import unidiff
import openai
from collections import defaultdict
from clang.cindex import Index, CursorKind
from typing import List, Dict, Union
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

Base = declarative_base()
# GPT_ENGINE = "gpt-3.5-turbo"
GPT_ENGINE = "gpt-4"

BOUNDARY_KIND_MAP = {
    "FUNCTION_DECL": CursorKind.FUNCTION_DECL,
    "CLASS_DECL": CursorKind.CLASS_DECL,
    "NAMESPACE": CursorKind.NAMESPACE,
    "MULTIPLE": None,
}


class CodeFile(Base):
    __tablename__ = 'code_files'
    id = Column(Integer, primary_key=True)
    path = Column(String, nullable=False)
    content = Column(String, nullable=False)
    prompt = Column(String, nullable=False)
    analysis = relationship("CodeAnalysis", back_populates="code_file")


class CodeAnalysis(Base):
    __tablename__ = 'code_analyses'
    id = Column(Integer, primary_key=True)
    response = Column(String, nullable=False)
    diff = Column(String, nullable=True)
    code_file_id = Column(Integer, ForeignKey('code_files.id'))
    code_file = relationship("CodeFile", back_populates="analysis")


def setup_database(database_url: str):
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    return engine


def read_file_contents(file_path: str) -> str:
    with open(file_path, 'r') as f:
        return f.read()


def read_config(config_path: str) -> Dict[str, Union[str, int]]:
    with open(config_path, 'r') as config_file:
        return json.load(config_file)


def generate_prompt(config: Dict[str, Union[str, int]], file_name: str, part_number: int = None, total_parts: int = None) -> str:
    if part_number is None or total_parts is None:
        return config["first_message_prompt"].format(config["project_name"], file_name)
    else:
        return config["part_message_prompt"].format(config["project_name"], file_name, part_number, total_parts)


def split_code_based_on_boundary(code: str, boundary_kinds: List[CursorKind]) -> List[str]:
    index = Index.create()
    with tempfile.NamedTemporaryFile(suffix=".cpp") as temp_file:
        temp_file_name = temp_file.name
        temp_file.write(code.encode())
        temp_file.flush()
        translation_unit = index.parse(temp_file_name, options=Index.PARSE_SKIP_FUNCTION_BODIES)
        temp_file.close()
    code_parts = []
    current_part_start = 0
    for cursor in translation_unit.cursor.walk_preorder():
        if cursor.kind in boundary_kinds and current_part_start < len(code):
            current_part_end = cursor.extent.start.offset
            code_parts.append(code[current_part_start:current_part_end])
            current_part_start = current_part_end
    if current_part_start < len(code):
        code_parts.append(code[current_part_start:])
    return code_parts


def filter_code_chunk(chunk: str) -> str:
    lines = chunk.splitlines()
    filtered_lines = []
    multi_line_comment = False
    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith("/*"):
            multi_line_comment = True
        if not multi_line_comment and not stripped_line.startswith("//") and not stripped_line == "":
            filtered_lines.append(line)
        if stripped_line.endswith("*/"):
            multi_line_comment = False
    return "\n".join(filtered_lines)


def split_code_into_chunks(code: str, max_code_length: int, boundary_kinds: List[CursorKind]) -> List[str]:
    code_parts = split_code_based_on_boundary(code, boundary_kinds)
    chunks = []
    chunk = ""
    for part in code_parts:
        filtered_part = filter_code_chunk(part)
        if len(chunk) + len(filtered_part) > max_code_length:
            chunks.append(chunk)
            chunk = ""
        chunk += filtered_part
    if chunk:
        chunks.append(chunk)
    return chunks    


def analyze_code(messages, config):
    openai.api_key = config["openai_api_key"]
    response_text = ""
    retries = 3
    while retries > 0:
        try:
            response = openai.Completion.create(
                engine= GPT_ENGINE,
                messages=[{"role": m["role"], "content": m["content"]} for m in messages],
                max_tokens=config["max_tokens"],
                n=config.get("n", 1),
                stop=config["stop"],
                temperature=config["temperature"],
            )
            response_text = response.choices[0].text
            if response_text.strip().endswith('```'):
                break
            else:
                last_line = response_text.strip().split('\n')[-1]
                follow_up_message = {
                    "role": "user",
                    "content": f"Please continue your previous response, starting with the line `{last_line}`\n\nBe sure to respond inside a code block so that the formatting is correct."
                }
                messages.append(follow_up_message)
        except Exception as e:
            print(f"Error encountered: {e}")
            retries -= 1
            time.sleep(config["analysis_interval"] * (2 ** (3 - retries)))
            if retries == 0:
                raise
    return response_text


def extract_patch_text(response_text: str) -> str:
    patch_regex = re.compile(r"```diff([\s\S]*?)```")
    match = patch_regex.search(response_text)
    if match:
        return match.group(1).strip()
    else:
        return ""


def apply_patch(original_code: str, patch: unidiff.PatchSet) -> str:
    original_code_lines = original_code.splitlines(True)
    modified_code_lines = list(unidiff.apply_patch(original_code_lines, patch))
    return "".join(modified_code_lines)


def generate_diff(original_code: str, final_code: str) -> str:
    diff = unidiff.unidiff(
        StringIO(original_code),
        StringIO(final_code),
        fromfile="original_code",
        tofile="final_code",
    )
    return "".join(list(diff))


def apply_suggested_changes(original_code: str, response_text: str) -> str:
    patch_text = extract_patch_text(response_text)
    patch = unidiff.PatchSet.from_string(patch_text)
    return apply_patch(original_code, patch)


def process_file(file_path: str, config: Dict[str, Union[str, int]], session) -> None:
    print(f"Processing file: {file_path}")
    output_folder = config["output_folder"]
    original_folder = config["project_folder"]
    combined_code = read_file_contents(file_path)
    relative_path = os.path.relpath(file_path, original_folder)
    output_file_path = os.path.join(output_folder, relative_path)
    output_file_dir = os.path.dirname(output_file_path)
    os.makedirs(output_file_dir, exist_ok=True)
    prompt_length = len(generate_prompt(config, os.path.basename(file_path), 1, 2))
    code_chunks = split_code_into_chunks(combined_code, config["max_code_length"] - prompt_length, [CursorKind.FUNCTION_DECL, CursorKind.CLASS_DECL, CursorKind.NAMESPACE])
    if not any(code_chunks):
        return
    messages = []
    for i, chunk in enumerate(code_chunks):
        prompt = generate_prompt(config, os.path.basename(file_path), i + 1, len(code_chunks)) + chunk
        messages.append({"role": "system", "content": prompt})
    response_text = analyze_code(messages, config)
    original_output_path = os.path.join(output_file_dir, 'original_' + os.path.basename(file_path))
    with open(original_output_path, 'w') as original_file:
        original_file.write(combined_code)
    suggestions_output_path = os.path.join(output_file_dir, 'suggestions_' + os.path.basename(file_path))
    with open(suggestions_output_path, 'w') as suggestions_file:
        suggestions_file.write(response_text)
    patch_text = extract_patch_text(response_text)
    patch = unidiff.PatchSet.from_string(patch_text)
    final_code = apply_patch(combined_code, patch)
    final_output_path = os.path.join(output_file_dir, os.path.basename(file_path))
    with open(final_output_path, 'w') as final_file:
        final_file.write(final_code)
    code_file = CodeFile(path=output_file_path, content=combined_code, prompt=messages[-1]["content"])
    code_analysis = CodeAnalysis(response=response_text, diff=generate_diff(combined_code, final_code), code_file=code_file)
    session.add(code_file)
    session.add(code_analysis)
    session.commit()


def extract_headers_from_cpp_file(file_path: str) -> List[str]:
    headers = []
    index = Index.create()
    translation_unit = index.parse(file_path, options=Index.PARSE_SKIP_FUNCTION_BODIES)
    for cursor in translation_unit.cursor.walk_preorder():
        if cursor.kind == CursorKind.INCLUSION_DIRECTIVE:
            headers.append(cursor.displayname)
    return headers


def process_header_file(header_path: str, source_files: List[str], config: Dict[str, Union[str, int]], session) -> None:
    print(f"Processing header file: {header_path}")
    header_code = read_file_contents(header_path)
    output_folder = config["output_folder"]
    original_folder = config["project_folder"]
    output_file_path = os.path.join(output_folder, os.path.relpath(header_path, original_folder))
    output_file_dir = os.path.dirname(output_file_path)
    os.makedirs(output_file_dir, exist_ok=True)
    header_messages = []
    last_response_text = None
    for source_file in source_files:
        source_code = read_file_contents(source_file)
        combined_code = f"{header_code}\n{source_code}"
        code_chunks = split_code_into_chunks(combined_code, config["max_code_length"], [CursorKind.FUNCTION_DECL, CursorKind.CLASS_DECL, CursorKind.NAMESPACE])
        if not code_chunks:
            return
        messages = []
        for i, chunk in enumerate(code_chunks):
            prompt = generate_prompt(config, os.path.basename(header_path), i + 1, len(code_chunks)) + chunk
            messages.append({"role": "system", "content": prompt})
        response_text = analyze_code(messages, config)
        last_response_text = response_text
        header_messages.append(messages[-1]["content"])
    original_output_path = os.path.join(output_file_dir, 'original_' + os.path.basename(header_path))
    with open(original_output_path, 'w') as original_file:
        original_file.write(header_code)
    suggestions_output_path = os.path.join(output_file_dir, 'suggestions_' + os.path.basename(header_path))
    with open(suggestions_output_path, 'w') as suggestions_file:
        suggestions_file.write(last_response_text)
    patch_text = extract_patch_text(last_response_text)
    patch = unidiff.PatchSet.from_string(patch_text)
    final_code = apply_patch(header_code, patch)
    final_output_path = os.path.join(output_file_dir, os.path.basename(header_path))
    with open(final_output_path, 'w') as final_file:
        final_file.write(final_code)
    code_file = CodeFile(path=output_file_path, content=header_code, prompt=messages[-1]["content"])
    code_analysis = CodeAnalysis(response=last_response_text, diff=generate_diff(header_code, final_code), code_file=code_file)  # Add the diff here
    session.add(code_file)
    session.add(code_analysis)
    session.commit()


def analyze_files(config: Dict[str, Union[str, int]], session) -> None:
    header_dependencies = defaultdict(set)
    for root, _, files in os.walk(config["project_folder"]):
        for file in files:
            if file.endswith('.cpp'):
                file_path = os.path.join(root, file)
                try:
                    headers = extract_headers_from_cpp_file(file_path)
                    for header in headers:
                        header_dependencies[header].add(file_path)
                    process_file(file_path, config, session)
                    time.sleep(config["analysis_interval"])
                except Exception as e:
                    print(f"Error processing file: {file_path}. Error: {e}")
    for header, source_files in header_dependencies.items():
        header_path = os.path.join(config["project_folder"], header)
        if os.path.exists(header_path):
            try:
                process_header_file(header_path, list(source_files), config, session)
                time.sleep(config["analysis_interval"])
            except Exception as e:
                print(f"Error processing header file: {header_path}. Error: {e}")


def generate_report(session, config: Dict[str, Union[str, int]]):
    report_file_name = f"{config['project_name']}_analysis.md"
    report_file_path = os.path.join(config["output_folder"], report_file_name)
    with open(report_file_path, "w") as report_file:
        report_file.write(f"# {config['project_name']} Analysis Report\n\n")
        code_files = session.query(CodeFile).all()
        for code_file in code_files:
            report_file.write(f"## File: `{code_file.path}`\n\n")
            report_file.write("### Original Code:\n\n")
            report_file.write("```cpp\n")
            report_file.write(code_file.content)
            report_file.write("\n```\n\n")
            for analysis in code_file.analysis:
                report_file.write("### ChatGPT Analysis:\n\n")
                report_file.write("#### Prompt:\n\n")
                report_file.write("```\n")
                report_file.write(analysis.code_file.prompt)
                report_file.write("\n```\n\n")
                report_file.write("#### Response:\n\n")
                report_file.write("```cpp\n")
                report_file.write(analysis.response)
                report_file.write("\n```\n\n")
            report_file.write("\n\n")  # Add this line
                

def check_openai_api_key(api_key: str):
    try:
        openai.api_key = api_key
        engines = openai.Engine.list()
        engine_ids = [engine.id for engine in engines["data"]]
        if GPT_ENGINE not in engine_ids:
            raise Exception(f"Required engine {GPT_ENGINE} is not available for your API key.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
        
                
def main(config_path: str) -> None:
    config = read_config(config_path)
    check_openai_api_key(config["openai_api_key"])
    os.makedirs(config["output_folder"], exist_ok=True)
    engine = setup_database(config["database_url"])
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        print(f"Analyzing project {config['project_name']}...")
        analyze_files(config, session)
        generate_report(session, config)
    finally:
        session.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python analyze_cpp_codebase.py <config_file_path>")
        sys.exit(1)
    try:
        os.system('clear')
        print('Now analyzing your codebase...')
        print(f"Using config file: {sys.argv[-1]}")
        config_file_path = sys.argv[-1]
        main(config_file_path)
    except KeyboardInterrupt:
        print("Interrupted by user. Exiting.")
        sys.exit(0)    
