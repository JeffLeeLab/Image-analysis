"""Report which scanned files are not yet images in OMERO.

Run by omero-upload.sh inside the omero environment, after it has logged in.
It reuses that CLI session, so it never asks for a password itself.

Usage: verify_upload.py USER@SERVER:PORT DATASET_NAME KEYS_FILE MISSING_FILE

KEYS_FILE holds one absolute path per line: the first file of each fileset
found by the Bio-Formats scan. DATASET_NAME may be empty, meaning the files
were imported without a target and only need to exist as images somewhere.
Paths still missing are written to MISSING_FILE. Exit status is 0 when nothing
is missing, 3 when something is.
"""
import sys

import omero.clients  # noqa: F401  (loads omero.sys)
from omero.cli import cli_login
from omero.rtypes import rlist, rstring
from omero.sys import ParametersI


def variants(path):
    # Tolerate the client path being recorded with or without a leading slash.
    return {path, path.lstrip("/")}


def path_params(user_id, paths):
    params = ParametersI()
    params.addLong("uid", user_id)
    params.map["paths"] = rlist([rstring(p) for p in sorted(paths)])
    return params


def main(target, dataset, keys_file, missing_file):
    with open(keys_file, encoding="utf-8") as f:
        keys = [line.rstrip("\n") for line in f if line.strip()]
    wanted = {v for key in keys for v in variants(key)}

    with cli_login(target) as cli:
        client = cli.get_client()
        query = client.sf.getQueryService()
        user_id = client.sf.getAdminService().getEventContext().userId

        params = path_params(user_id, wanted)
        if dataset:
            params.addString("dataset", dataset)
            hql = ("select distinct fe.clientPath from DatasetImageLink l "
                   "join l.child i join i.fileset fs join fs.usedFiles fe "
                   "where l.parent.name = :dataset "
                   "and l.parent.details.owner.id = :uid "
                   "and fe.clientPath in (:paths)")
        else:
            hql = ("select distinct fe.clientPath from Image i "
                   "join i.fileset fs join fs.usedFiles fe "
                   "where i.details.owner.id = :uid "
                   "and fe.clientPath in (:paths)")
        found = {row[0].val for row in query.projection(hql, params)}
        missing = [key for key in keys if not variants(key) & found]

        # --exclude=clientpath skips any file already recorded in a fileset,
        # so a missing file with a fileset will not be retried automatically.
        filesets = {}
        if missing:
            params = path_params(
                user_id, {v for key in missing for v in variants(key)})
            hql = ("select fs.id, fe.clientPath from Fileset fs "
                   "join fs.usedFiles fe where fs.details.owner.id = :uid "
                   "and fe.clientPath in (:paths)")
            for fs_id, client_path in query.projection(hql, params):
                filesets.setdefault(client_path.val.lstrip("/"), set()).add(
                    fs_id.val)

    with open(missing_file, "w", encoding="utf-8") as f:
        f.writelines(key + "\n" for key in missing)

    where = "Dataset %r" % dataset if dataset else "OMERO"
    print("Verified %d of %d file(s) in %s."
          % (len(keys) - len(missing), len(keys), where))
    for key in missing:
        ids = filesets.get(key.lstrip("/"))
        if ids:
            print("  MISSING (already recorded as Fileset %s, so re-runs skip"
                  " it): %s" % (", ".join(map(str, sorted(ids))), key))
        else:
            print("  MISSING: %s" % key)
    return 3 if missing else 0


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
