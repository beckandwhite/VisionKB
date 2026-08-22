# Introducing Multi-Environment Configuration

This update introduces a multi-environment configuration system and a managed workspace structure to allow for different project settings (e.g., DEV, QA, PRD) without modifying the core logic.

## Changes

### 1. Configuration Management
- **Environment-specific configurations**: Each environment now has its own dedicated configuration file located in `.workspace/<env_name>/config.json`.
- **Configuration parameters**:
    - `vision_model`: The Ollama model used for image analysis.
    - `embed_model`: The Ollama model used for vector embeddings.
    - `source_dir`: The primary directory for input images (e.g., iCloud Screenshots).
    - `temp_dir`: Temporary directory for processing.
    - `processed_limit`: Maximum number of images to process per run.
    - `max_dim`: Maximum dimension for image resizing to prevent OOM.
    - `save_every`: Frequency of checkpointing the tracker.
        - `TAG_LIST`: Space-separated list of allowed vision tags for the environment.
- **Centralized Loading**: The `config_loader.py` module resolves the environment from the `-env` command-line parameter, defaulting to `DEV`.

### 2. Managed Workspace
- **Workspace Directory**: A `.workspace/` directory is introduced to house environment-specific data and configuration.
- **Environment Isolation**:
    - Every environment has its own folder under `.workspace/`.
    - Data files like `_tracker.json` and `_annotations.jsonl` are now stored per-environment to prevent cross-contamination between different settings.

### 3. Impacted Components
- **Configuration Loader**: Refactored to handle per-environment file routing.
- **Classify Images**: Updated to pull all processing limits and model names from the active configuration.
- **Server/WebUI**: Updated to reflect the dynamic source directories.
- **Batch Processing/Reset Scripts**: Updated to support interactive/dynamic environment selection.

## Technical Details
- **Supported Environments**:
    - `DEV`: Development environment.
    - `QA`: Quality Assurance environment.
    - `PRD-iCloud-Screenshots`: Production for iCloud Screenshots.
    - `PRD-OneDrive-Pictures`: Production for OneDrive Pictures.
- **Configuration Merging**: While each file is independent, they share a common schema.
    Omitted values inherit the defaults used by `DEV`, including the screenshot tag
    taxonomy. Environment-specific profiles can add a domain-appropriate list
    without changing classifier logic.

## Workflow
To switch environments, pass the environment as a command-line parameter:
```bash
python3 classify_images.py -env DEV
```

If executed without specifying an environment, it'll list all the possible environments and then defaults to `DEV`. The configuration loader will automatically load the appropriate settings based on the current environment.
