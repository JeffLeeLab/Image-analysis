import ij.process.*;
import ij.plugin.filter.BackgroundSubtracter;
import java.io.*;
import java.nio.file.*;

/** Runs ImageJ's BackgroundSubtracter (rolling ball) on raw big-endian images.
 *
 *  Used by make_imagej_reference.py; not needed to use or test imagej_rolling_ball.py.
 *  Argument: a cases file, one case per line:
 *      type(float|short|byte) width height inPath outPath radius createBackground lightBackground presmooth
 *  The output is written in the input's type, big-endian.
 */
public class RunBackgroundSubtracter {
    public static void main(String[] args) throws Exception {
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            if (line.isBlank()) continue;
            String[] t = line.trim().split("\\s+");
            String type = t[0];
            int w = Integer.parseInt(t[1]), h = Integer.parseInt(t[2]);
            double radius = Double.parseDouble(t[5]);
            boolean create = Boolean.parseBoolean(t[6]);
            boolean light = Boolean.parseBoolean(t[7]);
            boolean presmooth = Boolean.parseBoolean(t[8]);
            ImageProcessor ip;
            try (DataInputStream in = new DataInputStream(new BufferedInputStream(new FileInputStream(t[3])))) {
                if (type.equals("float")) {
                    float[] px = new float[w * h];
                    for (int i = 0; i < px.length; i++) px[i] = in.readFloat();
                    ip = new FloatProcessor(w, h, px, null);
                } else if (type.equals("short")) {
                    short[] px = new short[w * h];
                    for (int i = 0; i < px.length; i++) px[i] = in.readShort();
                    ip = new ShortProcessor(w, h, px, null);
                } else {
                    byte[] px = new byte[w * h];
                    in.readFully(px);
                    ip = new ByteProcessor(w, h, px, null);
                }
            }
            // useParaboloid = false, correctCorners = false (paraboloid only)
            new BackgroundSubtracter().rollingBallBackground(ip, radius, create, light, false, presmooth, false);
            try (DataOutputStream out = new DataOutputStream(new BufferedOutputStream(new FileOutputStream(t[4])))) {
                if (ip instanceof FloatProcessor) for (float v : (float[]) ip.getPixels()) out.writeFloat(v);
                else if (ip instanceof ShortProcessor) for (short v : (short[]) ip.getPixels()) out.writeShort(v);
                else out.write((byte[]) ip.getPixels());
            }
        }
    }
}
