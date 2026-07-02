# @category TDS-Clone
# @runtime Jython
# decompile_fill.py - decompile cac ham lien quan FILL/ASK/SPREAD de tim nguon fill-ask.
# Pure ASCII (Jython 2.7).

print("=== decompile_fill.py START ===")
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

OUT = r"C:\Users\Phuc\Desktop\tickdata\ghidra_scripts\decompile_fill_out.txt"

prog = currentProgram
base = prog.getImageBase().getOffset()
af = prog.getAddressFactory().getDefaultAddressSpace()
fm = prog.getFunctionManager()

def resolve(va):
    # thu ca VA (base 0x400000) va RVA (base 0)
    for cand in (va, va - 0x400000 + base, va + base):
        try:
            a = af.getAddress(cand)
            f = fm.getFunctionContaining(a)
            if f is not None:
                return a, f
        except:
            pass
    # neu khong co function, thu tao tai dia chi
    try:
        a = af.getAddress(va)
        return a, fm.getFunctionContaining(a)
    except:
        return None, None

di = DecompInterface()
di.openProgram(prog)
mon = ConsoleTaskMonitor()

# Cac dia chi (VA base 0x400000) da RE:
TARGETS = [
    (0x00F7E3F0, "TickProc_setBidAsk"),    # set model.bid/ask tu outTick
    (0x00F7A4FB, "OrderCheck_SLTP"),        # kiem tra SL/TP dung ask 0x60d0
    (0x00F5EEF0, "TesterTickLoop"),         # vong lap tick goi FetchNextTick+TickProc
    (0x00F84FB0, "FetchNextTick"),          # sinh tick (ask=bid+model.328)
    (0x00F83C00, "FxtLoader_validate"),     # doc header + spread
    (0x00F83640, "TestGen_spreadSetup"),    # "spread set to %d"
]

print("ImageBase = 0x%X" % base)
out = open(OUT, "w")
out.write("ImageBase=0x%X\n\n" % base)
for va, name in TARGETS:
    a, f = resolve(va)
    if f is None:
        out.write("### %s @ 0x%X : KHONG tim thay function\n\n" % (name, va))
        print("%s: no function" % name)
        continue
    out.write("### %s  (req 0x%X -> func %s @ %s)\n" % (name, va, f.getName(), f.getEntryPoint()))
    try:
        res = di.decompileFunction(f, 60, mon)
        if res and res.decompileCompleted():
            out.write(res.getDecompiledFunction().getC())
        else:
            out.write("  [decompile failed: %s]\n" % (res.getErrorMessage() if res else "null"))
    except:
        import traceback
        out.write("  [exception]\n" + traceback.format_exc())
    out.write("\n\n" + "="*70 + "\n\n")
    print("done %s" % name)
out.close()
print("=== WROTE %s ===" % OUT)
