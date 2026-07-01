# @category TDS-Clone
# @runtime Jython
# decompile_candidates.py - Ghidra Jython headless script
# Pure ASCII (Jython 2.7 requires ASCII-only source without coding declaration)

print("=== decompile_candidates.py START ===")

try:
    from ghidra.app.decompiler import DecompInterface, DecompileOptions
    from ghidra.util.task import ConsoleTaskMonitor
    print("Imports OK")
except:
    import traceback
    print("IMPORT ERROR:")
    traceback.print_exc()

OUT = r"C:\Users\Phuc\Desktop\tickdata\ghidra_scripts\decompiled_output.txt"

# Addresses from signatures.json entry_rva field - correct Ghidra VAs (ImageBase=0x00400000).
CANDIDATES = [
    (0x00B36920, "Bid_00"),
    (0x00B73EC0, "Spread_01"),
    (0x00B83B40, "TestGen_02"),
    (0x00647F00, "TestGen_03"),
    (0x00B812B0, "TestGen_04"),
    (0x00B85BD0, "TestGen_05"),
    (0x00B834E0, "TestGen_06"),
    (0x00B83330, "TestGen_07"),
    (0x00B830E0, "TestGen_08"),
    (0x00B851D0, "TestGen_09"),
    (0x00A2EDA0, "tester_10"),
    (0x00B5FDD0, "tester_11"),
    (0x009DEEE0, "Tester_12"),
    (0x009F6C00, "Tester_13"),
    (0x009F7370, "Tester_14"),
    (0x009F7680, "Tester_15"),
    (0x009F8940, "Tester_16"),
    (0x009F8C80, "Tester_17"),
    (0x009EC650, "Tester_18"),
    (0x009EC8D0, "Tester_19"),
    (0x00B15FA0, "Tester_20"),
    (0x00B5B700, "Tester_21"),
    (0x00B58610, "Tester_22"),
    (0x00B5C3A0, "Tester_23"),
    (0x00B57B40, "Tester_24"),
    (0x00B606D0, "Tester_25"),
    (0x00B60A90, "Tester_26"),
    (0x00B5D440, "Tester_27"),
    (0x00B615F0, "Tester_28"),
    (0x00B66400, "Tester_29"),
    (0x00B65C30, "Tester_30"),
    (0x00B65C80, "Tester_31"),
    (0x00B73220, "Tester_32"),
    (0x00B73320, "Tester_33"),
    (0x00B72020, "Tester_34"),
    (0x00B78B10, "Tester_35"),
    (0x00B7DA80, "Tester_36"),
    (0x00B5DDA0, "Tester_37"),
    (0x00B7B420, "Tester_38"),
    (0x0093BD30, "Tester_39"),
    (0x007C2266, "Quote_40"),
    (0x008DBEE0, "Quote_41"),
    (0x00B775A0, "Quote_42"),
    # Parent functions - calls tester/TestGen candidates
    (0x00F5E580, "Parent_ticker_orch"),  # calls tester_10+Tester_27+Tester_37+TestGen_09
    (0x00F84D90, "Parent_tickgen_loop"), # calls TestGen_07+TestGen_08 twice
    (0x00F59570, "Parent_tick_dispatch"),# calls Tester_24+Tester_26+Tester_25
    (0x00F83C00, "Parent_pertick"),      # calls TestGen_06
    (0x00D0E410, "Parent_cod0_a"),       # calls Tester_25 (in .cod0 section)
    (0x00D0F3B0, "Parent_cod0_b"),       # calls Tester_25 (in .cod0 section)
    (0x00F84FB0, "Parent_tickgen2"),     # calls TestGen_08
    (0x00F86380, "Parent_tickgen3"),     # calls TestGen_05
    (0x00F807B0, "Parent_tickgen4"),     # calls TestGen_03
    (0x00F81670, "Parent_tickgen5"),     # calls TestGen_04
    (0x00F5DAD0, "Parent_TestD440"),     # calls Tester_27
    (0x00DECEF0, "Parent_Tester12"),     # calls Tester_12
    (0x00DDEC90, "Parent_Tester1314"),   # calls Tester_13+Tester_14
]


def run():
    prog = currentProgram
    addrFactory = prog.getAddressFactory().getDefaultAddressSpace()
    fm  = prog.getFunctionManager()
    mon = ConsoleTaskMonitor()

    print("Program: " + str(prog.getName()))
    print("ImageBase: " + str(prog.getImageBase()))
    print("Opening decompiler...")

    dc = DecompInterface()
    opts = DecompileOptions()
    dc.setOptions(opts)
    opened = dc.openProgram(prog)
    print("Decompiler opened: " + str(opened))

    ok_count = 0
    fail_count = 0

    with open(OUT, "w") as f:
        f.write("terminal.exe - Decompiled candidates\n")
        f.write("ImageBase: " + str(prog.getImageBase()) + "\n")
        f.write("=" * 70 + "\n\n")

        for va, label in CANDIDATES:
            try:
                addr = addrFactory.getAddress("0x%X" % va)
            except Exception as e:
                f.write("### %s @ 0x%X  [BAD ADDRESS: %s]\n\n" % (label, va, e))
                continue

            print("[%s] 0x%X..." % (label, va))

            try:
                disassemble(addr)
            except Exception as e:
                print("  disassemble err: " + str(e))

            func = fm.getFunctionAt(addr)
            if func is None:
                try:
                    func = createFunction(addr, label)
                    if func:
                        print("  created: " + func.getName())
                except Exception as e:
                    print("  createFunction err: " + str(e))

            if func is None:
                f.write("### %s @ 0x%X  [NO FUNCTION]\n\n" % (label, va))
                fail_count += 1
                continue

            try:
                result = dc.decompileFunction(func, 60, mon)
            except Exception as e:
                f.write("### %s @ 0x%X  [EXCEPTION: %s]\n\n" % (label, va, e))
                fail_count += 1
                continue

            if result and result.decompileCompleted():
                dc_func = result.getDecompiledFunction()
                c_code = dc_func.getC()
                f.write("### %s @ 0x%X\n" % (label, va))
                f.write(c_code)
                f.write("\n\n")
                ok_count += 1
                print("  OK (%d chars)" % len(c_code))
            else:
                msg = result.getErrorMessage() if result else "no result"
                f.write("### %s @ 0x%X  [FAILED: %s]\n\n" % (label, va, msg))
                fail_count += 1
                print("  FAILED: " + str(msg))

        f.write("Summary: %d OK, %d failed\n" % (ok_count, fail_count))

    dc.dispose()
    print("Done: %d OK, %d failed -> %s" % (ok_count, fail_count, OUT))


try:
    run()
except Exception as ex:
    import traceback
    print("FATAL ERROR: " + str(ex))
    traceback.print_exc()
    try:
        with open(OUT + ".err", "w") as fe:
            fe.write("FATAL: " + str(ex) + "\n")
            traceback.print_exc(file=fe)
    except:
        pass
