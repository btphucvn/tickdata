# test_jython.py - minimal test
# @category TDS-Clone
# @runtime Jython
import sys, traceback

OUT = r"C:\Users\Phuc\Desktop\tickdata\ghidra_scripts\jython_test_out.txt"

try:
    print("=== test_jython.py starting ===")
    print("sys.version: " + str(sys.version))
    print("currentProgram: " + str(currentProgram.getName()))
    print("imageBase: " + str(currentProgram.getImageBase()))

    with open(OUT, "w") as f:
        f.write("Jython works!\n")
        f.write("Program: " + str(currentProgram.getName()) + "\n")
        f.write("ImageBase: " + str(currentProgram.getImageBase()) + "\n")
        f.write("sys.version: " + str(sys.version) + "\n")

    print("File written to: " + OUT)
except Exception as e:
    print("ERROR: " + str(e))
    traceback.print_exc()
    try:
        with open(OUT + ".err", "w") as fe:
            fe.write("EXCEPTION: " + str(e) + "\n")
            traceback.print_exc(file=fe)
    except:
        pass

print("=== test_jython.py done ===")
