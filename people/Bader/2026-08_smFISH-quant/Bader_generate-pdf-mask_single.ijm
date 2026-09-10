// ImageJ Macro: 3D PDF segmentation of ONE image, run one-by-one for testing
// Works on the image that is already open and active in ImageJ.
// Same processing as Bader_generate-pdf-mask.ijm (batch version):
// pick the channel, rolling-ball background subtraction, median filter and
// auto threshold (all on the whole stack).
//
// The original image is left untouched -- the channel is duplicated first --
// so you can tweak the settings and re-run until the mask looks right.
// A max intensity projection of the mask is always shown for quality control.
//
// Use this when a MIPqc_ image from the batch run looks wrong: reopen that
// image, re-run here with different parameters until happy, then tick "Save".
// The file names are exactly what the batch script produces --
// PDFmask_<name>.tif and MIPqc_<name>.png -- but they are written to the SAME
// folder as the image rather than the PDFmask subfolder, so copying the pair
// into PDFmask/ overwrites just that image's batch output and leaves the rest
// alone.

if (nImages == 0) exit("No image open. Open an image first.");

orig = getImageID();
origTitle = getTitle();
origDir = getDirectory("image");			// "" if the image was never saved

// Derive the base name from the active image's own title rather than
// File.nameWithoutExtension, which refers to the last file opened and would
// give the wrong name if anything else has been opened since. Stripping the
// last extension matches what the batch script produces, so the files written
// here can be copied straight over the batch output.
base = getTitle();
dotIndex = lastIndexOf(base, ".");
if (dotIndex > 0) base = substring(base, 0, dotIndex);
getDimensions(width, height, channels, slices, frames);

methods = newArray("Default", "Huang", "Huang2", "Intermodes", "IsoData", "Li",
	"MaxEntropy", "Mean", "MinError(I)", "Minimum", "Moments", "Otsu",
	"Percentile", "RenyiEntropy", "Shanbhag", "Triangle", "Yen");

Dialog.create("PDF segmentation (single image)");
Dialog.addMessage("Image: " + origTitle + "  (" + channels + " channels, " + slices + " slices)");
Dialog.addNumber("Channel for PDF segmentation (1-based):", 1);
Dialog.addNumber("Rolling ball radius:", 50);
Dialog.addNumber("Median filter radius:", 2);
Dialog.addChoice("Auto threshold method:", methods, "Otsu");
Dialog.addCheckbox("Save mask TIFF + MIPqc PNG next to the image", false);
Dialog.show();

pdf_channel = Dialog.getNumber();
rolling = Dialog.getNumber();
median_radius = Dialog.getNumber();
method = Dialog.getChoice();
do_save = Dialog.getCheckbox();

if (pdf_channel < 1 || pdf_channel > channels)
	exit("Channel " + pdf_channel + " does not exist (image has " + channels + " channel(s)).");

// duplicate just the channel of interest, leaving the original open
selectImage(orig);
if (channels > 1)
	run("Duplicate...", "title=PDFmask_" + base + " duplicate channels=" + pdf_channel);
else
	run("Duplicate...", "title=PDFmask_" + base + " duplicate");

// "stack" options only apply when there is more than one slice
if (slices > 1)
	stackArg = " stack";
else
	stackArg = "";

run("Subtract Background...", "rolling=" + rolling + stackArg);
run("Median...", "radius=" + median_radius + stackArg);
if (slices > 1)
	run("Auto Threshold", "method=" + method + " white stack use_stack_histogram");
else
	run("Auto Threshold", "method=" + method + " white");

maskID = getImageID();
resetMinAndMax();

// max intensity projection of the mask, same as the batch QC image
if (slices > 1) {
	run("Z Project...", "projection=[Max Intensity]");
} else {
	run("Duplicate...", "title=MAX_PDFmask_" + base);	// single slice: nothing to project
}
mipID = getImageID();

if (do_save) {
	if (origDir == "")
		outDir = getDirectory("Choose where to save the mask");
	else
		outDir = origDir;				// same folder as the image

	selectImage(maskID);
	saveAs("Tiff", outDir + "PDFmask_" + base + ".tif");
	selectImage(mipID);
	saveAs("PNG", outDir + "MIPqc_" + base + ".png");
	print("Saved to " + outDir + " :  PDFmask_" + base + ".tif  and  MIPqc_" + base + ".png");
} else {
	print("Mask and MIP created, nothing saved (tick the save box to write them out).");
}

print("Settings -- channel: " + pdf_channel + ", rolling: " + rolling + ", median: " + median_radius + ", threshold: " + method);
