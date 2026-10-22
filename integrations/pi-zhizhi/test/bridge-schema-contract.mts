/** Explicit static cross-project contract. Reads trusted source JSON, never activates a model or tool. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { registeredTools, makeCaps } from './fixtures/bridge-fixture.ts';
import { assertToolSchemas } from '../src/codex-provider.ts';
const path=process.argv[2] ?? fileURLToPath(new URL('../../../projects/growth/src/ahadiff/knowledge_platform/model/pi_tools_v1.json',import.meta.url));
const wire=JSON.parse(await readFile(path,'utf8'));
assert.equal(wire.version,1);assert.equal(wire.tools.length,14);
const tools=registeredTools();const caps=makeCaps(tools);caps.tools=wire.tools;
assertToolSchemas(tools,tools,caps);
console.log(JSON.stringify({status:'passed',trusted_tools:tools.length,model_calls:0,tool_executions:0}));
