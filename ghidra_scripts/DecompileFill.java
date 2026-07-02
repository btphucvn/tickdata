// Decompile cac ham FILL/ASK/SPREAD -> tim nguon fill-ask. Java (Ghidra 12).
// @category TDS-Clone
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import java.io.PrintWriter;

public class DecompileFill extends GhidraScript {
    public void run() throws Exception {
        String OUT = "C:\\Users\\Phuc\\Desktop\\tickdata\\ghidra_scripts\\decompile_fill_out.txt";
        long base = currentProgram.getImageBase().getOffset();
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();

        long[] targets = {
            0x00F7E3F0L,  // TickProc: set model.bid/ask tu outTick
            0x00F7A4FBL,  // OrderCheck SL/TP dung ask 0x60d0
            0x00F7A050L,  // vung xu ly lenh khac
            0x00F5EEF0L,  // tester tick loop
            0x00F84FB0L,  // FetchNextTick
        };
        String[] names = {"TickProc","OrderCheckSLTP","OrderProc2","TesterTickLoop","FetchNextTick"};

        PrintWriter out = new PrintWriter(OUT);
        out.println("ImageBase=0x" + Long.toHexString(base));
        for (int i = 0; i < targets.length; i++) {
            Address a = toAddr(targets[i]);
            Function f = fm.getFunctionContaining(a);
            if (f == null) {
                // thu disassemble + tao function tai dia chi
                try { disassemble(a); } catch (Exception e) {}
                try { f = createFunction(a, names[i]); } catch (Exception e) {}
                if (f == null) f = fm.getFunctionContaining(a);
            }
            if (f == null) {
                out.println("### " + names[i] + " @ 0x" + Long.toHexString(targets[i]) + " : NO FUNCTION");
                out.println();
                continue;
            }
            out.println("### " + names[i] + "  req=0x" + Long.toHexString(targets[i])
                        + " func=" + f.getName() + " @ " + f.getEntryPoint());
            try {
                DecompileResults res = di.decompileFunction(f, 90, monitor);
                if (res != null && res.decompileCompleted())
                    out.println(res.getDecompiledFunction().getC());
                else
                    out.println("  [decompile failed: " + (res==null?"null":res.getErrorMessage()) + "]");
            } catch (Exception e) {
                out.println("  [exception] " + e);
            }
            out.println();
            out.println("======================================================================");
            out.println();
        }
        out.close();
        println("WROTE " + OUT);
    }
}
