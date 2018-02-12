def list_dir_files(path: str, suffix: str= '') -> list:
    """
    Lists all files (and only files) in a directory, or return [path] if path is a file itself.
    :param path: Directory or a file
    :param suffix: Optional suffix to match (case insensitive). Default is none.
    :return: list of absolute paths to files
    """
    import os
    from pathlib import Path

    if suffix:
        suffix = suffix.lower()
    if Path(path).is_file():
        files = [path]
    else:
        files = []
        for f in os.listdir(path):
            file_path = os.path.join(path, f)
            if Path(file_path).is_file():
                if not suffix or f.lower().endswith(suffix):
                    files.append(file_path)
    return files
