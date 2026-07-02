// Dump cac lenh TickProc ghi close (order+0x78) va load SL/TP (order+0x30/0x38)
// -> lay dia chi + bytes de tao pattern sigscan runtime (patch gap-fill).
// @category TDS-Clone
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import java.io.PrintWriter;

public class DumpClamp extends GhidraScript {
    public void run() throws Exception {
        String OUT = "C:\\Users\\Phuc\\Desktop\\tickdata\\ghidra_scripts\\clamp_out.txt";
        PrintWriter out = new PrintWriter(OUT);
        Listing lst = currentProgram.getListing();
        // vung SL/TP fill trong TickProc
        long lo = 0x00F7E3F0L, hi = 0x00F7F800L;
        Address a = toAddr(lo), end = toAddr(hi);
        InstructionIterator it = lst.getInstructions(a, true);
        while (it.hasNext()) {
            Instruction ins = it.next();
            if (ins.getAddress().getOffset() >= hi) break;
            String s = ins.toString();
            boolean w78 = s.contains("0x78]") ;
            boolean sl  = s.contains("0x30]");
            boolean tp  = s.contains("0x38]");
            if (w78 || sl || tp) {
                byte[] b = ins.getBytes();
                StringBuilder hx = new StringBuilder();
                for (byte x : b) hx.append(String.format("%02x ", x & 0xff));
                String tag = w78 ? "  <== CLOSE(0x78)" : (sl ? "  <== SL(0x30)" : "  <== TP(0x38)");
                out.printf("%08x  %-40s | %s%s%n", ins.getAddress().getOffset(), s, hx.toString().trim(), tag);
            }
        }
        // them: disasm 20 lenh dau cua func 0xF7C740 (close-finalize)
        out.println("\n=== func 0xF7C740 (close-finalize) 24 lenh dau ===");
        Address c = toAddr(0x00F7C740L);
        Instruction ci = lst.getInstructionAt(c);
        for (int i = 0; i < 24 && ci != null; i++) {
            byte[] b = ci.getBytes(); StringBuilder hx = new StringBuilder();
            for (byte x : b) hx.append(String.format("%02x ", x & 0xff));
            out.printf("%08x  %-40s | %s%n", ci.getAddress().getOffset(), ci.toString(), hx.toString().trim());
            ci = ci.getNext();
        }
        out.close();
        println("WROTE " + OUT);
    }
}
