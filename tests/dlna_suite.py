"""Complete source regression suite in the disposable experimental test image."""
import glob
import subprocess
import sys

subprocess.run(['node', '--test', '--test-concurrency=1', *sorted(glob.glob('tests/*.test.js'))], check=True)
subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'], check=True)
subprocess.run([sys.executable, 'tests/integration.py'], check=True)
