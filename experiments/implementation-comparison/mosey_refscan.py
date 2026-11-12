# Ghidra headless post-analysis script: list direct references and containing
# functions for selected Rust log strings in the Mosey FFI binary.
from ghidra.app.decompiler import DecompInterface

targets = [
    (0x0000af96, "Setting schedule:"),
    (0x0000ba2c, "Failed to queue flushed frame:"),
    (0x000133ff, "Failed to queue data frame:"),
]
listing = currentProgram.getListing()
fm = currentProgram.getFunctionManager()
refs = currentProgram.getReferenceManager()
decomp = DecompInterface()
decomp.openProgram(currentProgram)

for offset, label in targets:
    addr = toAddr(offset)
    print("TARGET 0x%x %s" % (offset, label))
    found = False
    for ref in refs.getReferencesTo(addr):
        found = True
        source = ref.getFromAddress()
        func = fm.getFunctionContaining(source)
        print("  REF %s type=%s function=%s" % (source, ref.getReferenceType(), func))
        if func is not None:
            result = decomp.decompileFunction(func, 30, monitor)
            if result and result.decompileCompleted():
                print("  DECOMPILE %s" % func.getEntryPoint())
                print(result.getDecompiledFunction().getC())
    if not found:
        print("  NO_DIRECT_REFERENCE")

for name in ["mosey_start_5", "mosey_update", "mosey_get_hw_cap"]:
    funcs = currentProgram.getSymbolTable().getSymbols(name)
    for sym in funcs:
        func = fm.getFunctionAt(sym.getAddress())
        print("EXPORT %s %s function=%s" % (name, sym.getAddress(), func))
        if func is not None:
            result = decomp.decompileFunction(func, 30, monitor)
            if result and result.decompileCompleted():
                print("  DECOMPILE %s" % func.getEntryPoint())
                print(result.getDecompiledFunction().getC())
