"""Run suites in separate processes because legacy Agent tests install module stubs."""
import os
import subprocess
import sys
from pathlib import Path
root=Path(__file__).resolve().parent
test_env = dict(os.environ)
test_env['PYTHONPATH'] = str(root.parent) + os.pathsep + test_env.get('PYTHONPATH', '')
for name in ['test_llm_output.py','test_autonomy_policy.py','test_autonomous_workflow_contract.py','test_security_integration.py','test_security_production.py','test_account_security.py','test_conversation_agent.py','test_dataset_import.py','test_job_workspaces.py']:
    subprocess.run([sys.executable,str(root/name)],check=True,env=test_env)
print('All backend suites passed.')
