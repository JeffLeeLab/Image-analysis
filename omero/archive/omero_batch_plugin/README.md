# OMERO Batch Plugin

This plugin provides a way to run batch jobs on images stored in OMERO server using FIJI.
Note that the actual computation is done on the your local machine, not on the OMERO server.

## Installation

1. Make sure you have the latest version of FIJI installed.
2. If you don't have the OMERO plugin for FIJI.
    - Follow the instructions at [OMERO plugin for FIJI](https://omero-guides.readthedocs.io/en/latest/fiji/docs/installation.html).
3. Download the simple-omero-client jar from the [simple-omero-client repo](https://github.com/GReD-Clermont/simple-omero-client/releases/tag/5.19.0)
    - Get the `simple-omero-client-5.19.0-jar-with-dependencies.jar` file.
4. Download the omero_batch-plugin jar from the [omero_batch-plugin repo](https://github.com/GReD-Clermont/omero_batch-plugin/releases/tag/2.1.0)
    - Get the `omero_batch-plugin-2.1.0.jar` file.
5. Place both jar files in the `plugins` directory of your FIJI installation.

## Usage

1. Open FIJI and go to `Plugins > OMERO > Batch process ...`. 
2. Initiate a connection to your OMERO server by clicking the "Connect" button.

```
    Host: omerodavisvm.mvls.gla.ac.uk
    Port: 4064
    User: <your guid>
    Password: <your omero password>
```

3. Set `Source: Where to get images to analyse:` to `OMERO`, and select your `Project` and `Dataset`.
4. Browse `.ijm` macro file for usage and select outputs.
5. Set `Where to save results:` and click `Start`.