# C++ Code Analysis with GPT-4

This project is designed to automatically parse C++ code files, generate sub-files containing individual functions or function groups, create GPT-4 prompts, communicate with the GPT-4 API, and save the data to an SQLite database using SQLAlchemy ORM.

## Requirements

- Python 3.7 or newer
- Clang
- OpenAI API key

## Installation

1. Clone the repository.
2. Install the dependencies by running:

```
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install wheel
pip install -r requirements.txt
```


## Usage

You can run the script from the command line:

```
python analyze_cpp_codebase.py <config_file_path>
```

Here is a sample configuration file in JSON format for the updated code:

{
    "project_name": "pastel",
    "project_folder": "/home/ubuntu/pastel_source/pastel/src/",
	"output_folder": "output_files/pastel/src/",
    "database_url": "sqlite:///pastel_analysis.sqlite",
    "max_code_length": 5000,
    "split_boundary": "FUNCTION_DECL",
    "analysis_interval": 5,
    "openai_api_key": "sk-XXXXXXXX",
    "first_message_prompt": "Below is a C++ code file from the {} project, called `{}`. Please look for bugs and potential problems in the code and propose specific code changes to address them (you can also give me other ideas for improvements to code reliability and performance, but your focus should be on detecting even subtle bugs and proposing concrete changes to fix them, without introducing any new bugs in the process.):\n\n",
    "part_message_prompt": "Here is part {} of {} of the related code file from the {} project, called `{}`. Please give the same kind of analysis, looking for bugs and potential problems and proposing specific code changes to address them. Please focus more on potential bugs and fixes. Here is the code:\n\n",
    "max_tokens": 3000,
    "n": 1,
    "stop": null,
    "temperature": 0.5
}


This will analyze the C++ code files in the specified project folder and save the results to the specified SQLite database.

This configuration file includes the following settings:

- `project_name`: The name of your project.
- `project_folder`: The path to the folder containing your project's C++ source files.
- `output_folder`: The path to the folder where the output files will be saved.
- `database_url`: The URL for the SQLite database where the analysis results will be stored.
- `max_code_length`: The maximum number of tokens allowed for code analysis.
- `split_boundary`: The boundary for splitting code chunks (e.g., "FUNCTION_DECL").
- `analysis_interval`: The interval for analysis in seconds (e.g., 5 seconds).
- `openai_api_key`: Your OpenAI API key.
- `first_message_prompt`: The prompt for the first analysis message.
- `part_message_prompt`: The prompt for subsequent analysis messages when the code is split into parts.
- `max_tokens`: The maximum number of tokens allowed in a response from the GPT-4 API.
- `n`: The number of responses to generate from the GPT-4 API for each prompt.
- `stop`: An optional sequence to stop the API response generation. Leave as null if not required.
- `temperature`: The sampling temperature to use for the GPT-4 API response generation. Lower values make the output more deterministic, while higher values make it more diverse.

Make sure to replace the placeholder values with your actual paths and API key before using this configuration file. Save this JSON file to a location accessible by your script, and provide the path to the file when running the script.

Alternatively, you can run the script using the provided Visual Studio Code `launch.json` configuration. Update the arguments in the `launch.json` file according to your project requirements.

## Contributing

Contributions to this project are welcome. Please submit a pull request or create an issue to discuss your proposed changes.

## License

This project is released under the MIT License.
