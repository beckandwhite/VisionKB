# New User Journey: Fast-Feedback Image Processing

This guide explains how to quickly process a folder of screenshots and see the results with a fast feedback loop.

## Quick Start (10-Image sample)

If you have a folder of images (e.g., `/Users/User1/Screenshots`) and want to see how the system handles them, follow these steps:

### 1. Initialize your environment
 First, create a new environment to manage your settings:
 ```bash
 ./environment_admin.sh init
 ```
 This seeds `.workspace/config.json` from `.workspace/config.template.json`. It prompts only
 for the environment name and the source folder; every other value is inherited
 from the template. Pass them instead to skip the prompts:
 ```bash
 ./environment_admin.sh init <NAME> --source /Users/User1/Screenshots
 ```
 To remove an environment again: `./environment_admin.sh decomm <NAME>`.

### 2. Process a sample run
Run the backend on your specific folder, limited to the first 10 images for quick feedback:
```bash
python3 backend.py --screenshot-dir /Users/User1/Screenshots --count 10
```

### 3. View Results
Once the processing is complete, the results will be available in the local database and the exported JSON files. You can then open the frontend to browse your tagged images and generated captions.

## Key Features
- **Fast Feedback:** By using the `--count` flag, you can validate the vision model's output without waiting for a full-folder scan.
- **Automated Tracking:** The system keeps track of which images have been processed and which labels were assigned, ensuring you can resume later.
- **Knowledgebase Integration:** Every processed image is automatically injected into the SQLite wiki for easy searching and discovery.
