#!/usr/bin/env python
"""Trusted OMERO entry point. Set the two deployment paths before uploading."""
# These paths belong to the processor host/container, not the user's computer.
ADAPTER_DIRECTORY = "/opt/omero-bgsub/0.1.0/adapter"
OPERATOR_CONFIG = "/etc/omero-bgsub/0.1.0.json"

import sys
import omero.scripts as scripts
from omero.rtypes import rlong, rstring

RADIUS_HELP = ("Radius in XY pixels. Set radius to 0 to skip background subtraction for that channel. "
               "The channel is kept unchanged in the output image. If all radii are 0, a copy of the image is created.")


def parameters():
    params = [
        scripts.String("Data_Type", optional=False, default="Image", values=["Image", "Dataset"], grouping="1",
                       description="One/multiple image IDs OR exactly one dataset ID."),
        scripts.List("IDs", optional=False, grouping="1.1", description="Positive IDs of the selected input type.").ofType(rlong(0)),
        scripts.String("Run_Mode", optional=False, default="Preview", values=["Preview", "Process"], grouping="2"),
        scripts.String("Name_Filter", grouping="2.1", description="Blank includes all selected candidates, including earlier results."),
        scripts.String("Name_Match_Mode", default="Contains", values=["Contains", "Exact", "Wildcard"], grouping="2.2"),
        scripts.Bool("Case_Sensitive", default=True, grouping="2.3"),
        scripts.String("Channel_Match_Mode", default="Name", values=["Name", "Index"], grouping="3",
                       description="Exact stored names or zero-based indices (0, 1, 2...). Count/order must match across images."),
    ]
    for i in range(1, 9):
        params.extend([
            scripts.String(f"Rule_{i}_Channel", grouping=f"3.{i}",
                           description="Channel name or zero-based index; leave both fields absent to disable this row."),
            scripts.Double(f"Rule_{i}_Radius", min=0, grouping=f"3.{i}.1", description=RADIUS_HELP),
        ])
    params.extend([
        scripts.String("Output_Mode", default="Existing_Dataset", values=["Existing_Dataset", "New_Dataset"], grouping="4"),
        scripts.Long("Output_Dataset_ID", min=1, grouping="4.1", description="Existing_Dataset only. All results go into this dataset."),
        scripts.String("New_Dataset_Name", grouping="4.2", description="New_Dataset only. Enter a name; OMERO assigns the new ID."),
        scripts.Long("Output_Project_ID", min=1, grouping="4.3", description="New_Dataset only; optional project. Absent means no project link."),
        scripts.String("Resume_Run_ID", grouping="5", description="Run ID from an earlier Process report; supply the same original parameters."),
    ])
    return params


def run():
    client = scripts.client(
        "Batch_Rolling_Ball_Background.py",
        "Rolling-ball subtraction on raw planes. " + RADIUS_HELP +
        " Preview first. Fixed output names: basename_bgs-r0-r1-....tif. "
        "Presmoothing on; negatives clipped for corrected channels; original dtype preserved. "
        "Outputs are OMERO images, not TIFF-file exports.",
        *parameters(), version="0.1.0", authors=["Jeff Lee Lab"],
        institutions=["Jeff Lee Lab"], contact="", namespaces=["org.jeffleelab.omero.bgsub/v1"])
    conn = None
    try:
        # scripts.client handles parameter discovery before reaching this import.
        sys.path.insert(0, ADAPTER_DIRECTORY)
        from omero.gateway import BlitzGateway
        from bgsub_adapter.application import execute
        conn = BlitzGateway(client_obj=client)
        execute(client, conn, OPERATOR_CONFIG)
    except Exception as exc:
        client.setOutput("Message", rstring(type(exc).__name__ + ": " + str(exc)[:3000]))
        raise
    finally:
        if conn is not None:
            conn.close()
        client.closeSession()


if __name__ == "__main__":
    run()
