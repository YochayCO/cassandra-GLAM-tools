def fingerprint_matches(stored_files, stored_subcats, replica_files, replica_subcats):
    if stored_files is None or stored_subcats is None:
        return False
    return stored_files == replica_files and stored_subcats == replica_subcats
