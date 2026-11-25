import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/deploy.yml")


def workflow_jobs(text):
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "jobs:")
    jobs = {}
    name = None
    buf = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        if (
            line.startswith("  ")
            and not line.startswith("   ")
            and line.rstrip().endswith(":")
            and not line.strip().startswith("-")
        ):
            if name:
                jobs[name] = "\n".join(buf)
            name = line.strip()[:-1]
            buf = [line]
        elif name:
            buf.append(line)
    if name:
        jobs[name] = "\n".join(buf)
    return jobs


class DeployWorkflowTest(unittest.TestCase):
    def test_pages_deploy_is_a_separate_job_from_the_artifact_upload(self):
        jobs = workflow_jobs(WORKFLOW.read_text())
        upload_jobs = [
            name
            for name, body in jobs.items()
            if "actions/upload-pages-artifact@" in body
        ]
        deploy_jobs = [
            name for name, body in jobs.items() if "actions/deploy-pages@" in body
        ]
        self.assertEqual(upload_jobs, ["build"])
        self.assertEqual(deploy_jobs, ["deploy"])
        self.assertNotIn("actions/upload-pages-artifact@", jobs["deploy"])
        self.assertNotIn("actions/deploy-pages@", jobs["build"])
        self.assertIn("python generate_html.py", jobs["build"])
        self.assertRegex(jobs["deploy"], r"needs:\s*build")


if __name__ == "__main__":
    unittest.main()
