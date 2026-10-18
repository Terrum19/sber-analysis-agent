// Исполнитель кода агента в Pyodide (WASM).
// Запуск: node --experimental-permission ... runner.mjs <папка песочницы>
// stdin: JSON {"code": "..."}; stdout: одна строка JSON {"ok", "error", "stdout"}.
// Код видит только /sandbox (папка из argv), сети и JS-моста у него нет.

import { constants as fsConstants, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadPyodide } from "pyodide";

// Под --permission process.binding запрещён, а NODEFS берёт из него только флаги open().
process.binding = (name) => {
  if (name === "constants") return { fs: fsConstants };
  throw new Error(`process.binding(${name}) is not available in the sandbox`);
};

const here = dirname(fileURLToPath(import.meta.url));
const workdir = process.argv[2];
const request = JSON.parse(readFileSync(0, "utf8"));
const output = [];
const reply = (body) => process.stdout.write(JSON.stringify({ ...body, stdout: output.join("\n") }) + "\n");

const LOCKDOWN = `
def _lockdown():
    import builtins, importlib.abc, sys
    roots = {"js", "pyodide_js", "micropip"}
    names = {"pyodide.code", "pyodide.ffi", "pyodide.http", "pyodide.webloop"}

    def blocked(name):
        return name in names or name.split(".")[0] in roots

    for name in list(sys.modules):
        if blocked(name):
            del sys.modules[name]
    pyodide = sys.modules.get("pyodide")
    if pyodide is not None:
        for attr in ("code", "ffi", "http", "webloop"):
            pyodide.__dict__.pop(attr, None)

    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if blocked(name):
                raise ImportError(f"module {name!r} is not available in the sandbox")
            return None

    sys.meta_path.insert(0, Finder())
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if blocked(name) or any(blocked(f"{name}.{item}") for item in fromlist or ()):
            raise ImportError(f"module {name!r} is not available in the sandbox")
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded_import

    import os, posix, socket, subprocess, _socket

    def denied(*args, **kwargs):
        raise PermissionError("this operation is not available in the sandbox")

    for attr in ("system", "popen", "fork", "forkpty", "kill", "killpg", "symlink", "link",
                 "posix_spawn", "posix_spawnp", "execv", "execve", "execl", "execle", "execlp",
                 "execlpe", "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
                 "spawnv", "spawnve", "spawnvp", "spawnvpe"):
        for module in (os, posix):
            if hasattr(module, attr):
                setattr(module, attr, denied)
    for attr in ("Popen", "run", "call", "check_call", "check_output", "getoutput",
                 "getstatusoutput"):
        setattr(subprocess, attr, denied)
    for attr in ("socket", "SocketType", "create_connection", "create_server", "getaddrinfo",
                 "gethostbyname", "gethostbyname_ex", "socketpair", "fromfd"):
        setattr(socket, attr, denied)
        if hasattr(_socket, attr):
            setattr(_socket, attr, denied)

_lockdown()
del _lockdown
`;

const COLLECT = `
import pandas as _pd
_value = globals().get("result")
if _value is None:
    raise ValueError("code must assign the output to a variable named result")
if isinstance(_value, _pd.Series):
    _value = _value.to_frame()
elif not isinstance(_value, _pd.DataFrame):
    _value = _pd.DataFrame(_value)
_value.to_csv("/sandbox/result.csv", index=False)
`;

try {
  const pyodide = await loadPyodide({
    indexURL: join(here, "node_modules", "pyodide"),
    stdout: (line) => output.push(line),
    stderr: (line) => output.push(line),
    env: { HOME: "/sandbox" },
  });
  await pyodide.loadPackage("pandas", { messageCallback: () => {} });
  await pyodide.loadPackagesFromImports(request.code, { messageCallback: () => {} });

  for (const op of ["symlink", "readlink"]) {
    pyodide.FS.filesystems.NODEFS.node_ops[op] = () => {
      throw new pyodide.FS.ErrnoError(63);
    };
  }
  pyodide.FS.mkdirTree("/sandbox");
  pyodide.FS.mount(pyodide.FS.filesystems.NODEFS, { root: workdir }, "/sandbox");
  pyodide.FS.chdir("/sandbox");

  for (const name of ["fetch", "WebSocket", "XMLHttpRequest", "EventSource"]) {
    globalThis[name] = undefined;
  }
  pyodide.runPython(LOCKDOWN);

  const scope = pyodide.globals.get("dict")();
  pyodide.runPython('import pandas as pd\ndf = pd.read_csv("/sandbox/input.csv")', { globals: scope });
  await pyodide.runPythonAsync(request.code, { globals: scope });
  pyodide.runPython(COLLECT, { globals: scope });
  reply({ ok: true, error: null });
} catch (error) {
  reply({ ok: false, error: String(error?.message ?? error) });
}
