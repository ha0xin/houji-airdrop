import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;

public class MoseyRefscan extends ghidra.app.script.GhidraScript {
    private void inspect(byte[] needle, String label, DecompInterface decomp) throws Exception {
        Address start = currentProgram.getMinAddress();
        Address end = currentProgram.getMaxAddress();
        Address hit = currentProgram.getMemory().findBytes(start, end, needle, null, true, monitor);
        println("TARGET " + label + " address=" + hit);
        if (hit == null) return;
        boolean found = false;
        for (int i = 0; i < needle.length; i++) {
            Address byteAddr = hit.add(i);
            for (Reference ref : getReferencesTo(byteAddr)) {
                found = true;
                Address from = ref.getFromAddress();
                Function func = getFunctionContaining(from);
                println("  REF " + from + " -> " + byteAddr + " type=" + ref.getReferenceType() + " function=" + func);
                if (func != null) {
                    DecompileResults result = decomp.decompileFunction(func, 20, monitor);
                    if (result != null && result.decompileCompleted()) {
                        println("  DECOMPILE " + func.getEntryPoint());
                        println(result.getDecompiledFunction().getC());
                    }
                }
            }
        }
        if (!found) println("  NO_DIRECT_REFERENCE_TO_STRING_BYTES");
    }

    @Override
    public void run() throws Exception {
        println("PROGRAM " + currentProgram.getName());
        println("IMAGE_BASE " + currentProgram.getImageBase());
        println("LANGUAGE " + currentProgram.getLanguage().getLanguageID());
        println("COMPILER_SPEC " + currentProgram.getCompilerSpec().getCompilerSpecID());
        for (MemoryBlock block : currentProgram.getMemory().getBlocks()) {
            println("BLOCK " + block.getName() + " " + block.getStart() + ".." + block.getEnd() + " size=" + block.getSize() + " read=" + block.isRead() + " write=" + block.isWrite() + " execute=" + block.isExecute());
        }
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        inspect("Setting schedule:".getBytes("US-ASCII"), "Setting schedule:", decomp);
        inspect("Failed to queue flushed frame:".getBytes("US-ASCII"), "Failed to queue flushed frame:", decomp);
        inspect("Failed to queue data frame:".getBytes("US-ASCII"), "Failed to queue data frame:", decomp);
    }
}
