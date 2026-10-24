// Ghidra headless postScript (Java GhidraScript): decompile one function, or
// list functions, writing the result to a file. Driven by ghidra.py; runs
// INSIDE Ghidra, compiled on the fly by the bundled JDK. Java is used, not
// Python, because Ghidra 12 dropped Jython and a .py script needs PyGhidra
// enabled; a .java GhidraScript always runs headless with no extra setup.
//
// Script args: <selector> <out_path>
//   selector = a function name, an 0xADDRESS, or the literal ::list::
//
// Spine, kept legible in the output: the decompiled C is a HYPOTHESIS (a reading
// aid; Ghidra renames and guesses types); the instruction lines under it
// (address + bytes) are GROUND TRUTH.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.util.task.ConsoleTaskMonitor;

import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.List;

public class GhidraDecompile extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("GhidraDecompile needs <selector> <out_path>");
            return;
        }
        String selector = args[0];
        String outPath = args[1];
        StringBuilder sb = new StringBuilder();

        if (selector.equals("::list::")) {
            FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
            while (it.hasNext()) {
                Function fn = it.next();
                sb.append(fn.getEntryPoint()).append('\t').append(fn.getName())
                  .append('\t').append(fn.getBody().getNumAddresses()).append('\n');
            }
        } else {
            Function fn = resolve(selector);
            if (fn == null) {
                sb.append("NO_FUNCTION\t").append(selector).append('\n');
            } else {
                sb.append("// function ").append(fn.getName()).append(" @ ")
                  .append(fn.getEntryPoint()).append('\n');
                DecompInterface di = new DecompInterface();
                try {
                    di.openProgram(currentProgram);
                    DecompileResults res = di.decompileFunction(fn, 120, new ConsoleTaskMonitor());
                    if (res != null && res.decompileCompleted()) {
                        sb.append("// ---- decompiled C (hypothesis: a reading aid) ----\n");
                        sb.append(res.getDecompiledFunction().getC()).append('\n');
                    } else {
                        sb.append("// decompilation failed: ")
                          .append(res == null ? "no result" : res.getErrorMessage()).append('\n');
                    }
                } finally {
                    di.dispose();
                }
                sb.append("// ---- instructions (ground truth: address + bytes) ----\n");
                Listing listing = currentProgram.getListing();
                InstructionIterator ins = listing.getInstructions(fn.getBody(), true);
                while (ins.hasNext()) {
                    Instruction i = ins.next();
                    StringBuilder hx = new StringBuilder();
                    for (byte x : i.getBytes()) {
                        hx.append(String.format("%02x", x & 0xff));
                    }
                    sb.append(i.getAddress()).append("  ").append(hx).append("  ")
                      .append(i.toString()).append('\n');
                }
            }
        }

        try (Writer w = new OutputStreamWriter(new FileOutputStream(outPath), StandardCharsets.UTF_8)) {
            w.write(sb.toString());
        }
        println("GhidraDecompile wrote " + outPath);
    }

    private Function resolve(String selector) {
        if (selector.toLowerCase().startsWith("0x")) {
            Address a = currentProgram.getAddressFactory().getAddress(selector);
            if (a == null) {
                return null;
            }
            Function fn = getFunctionAt(a);
            return fn != null ? fn : getFunctionContaining(a);
        }
        List<Function> fns = getGlobalFunctions(selector);
        return fns.isEmpty() ? null : fns.get(0);
    }
}
