# Lock Implementation

The `.pipeline.lock` file is maintained to ensure serial execution of pipeline tasks. Even with a serial file-by-file processing strategy, the lock is necessary to:

- **Prevent Race Conditions**: Protect shared state (e.g., `config.json`, shared databases) from concurrent writes if multiple instances are triggered simultaneously.
- **Handle Accidental Double-Triggers**: Prevent multiple workers from attempting to process the same file or update the same metadata.
- **Detect Stale Runs**: By recording a PID in the lock file, we can identify and debug hung processes that may be holding a lock.

The lock is acquired during the execution of any "Writer" task and released upon completion.
