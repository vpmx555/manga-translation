"""Startup must identify the worker across Windows Python redirectors."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deployment/runpod'))
import local_review


class ReviewStartupTests(unittest.TestCase):
    def test_worker_pid_can_differ_from_launcher_pid(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch=Path(temporary).resolve()
            child=Mock(pid=1001)
            child.poll.return_value=None
            def spawn(*args,**kwargs):
                local_review.atomic_json(batch/'local-review-worker.json',
                    {'pid':2002,'batch':str(batch),'status':'watching',
                     'launch_token':kwargs['env']['MANGA_LOCAL_REVIEW_LAUNCH_TOKEN']})
                return child
            with patch.object(local_review.subprocess,'Popen',side_effect=spawn):
                result=local_review.start_watch(batch)
            self.assertEqual(result['pid'],2002)
            self.assertEqual(result['launcher_pid'],1001)

    def test_stale_state_cannot_confirm_a_new_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch=Path(temporary).resolve()
            local_review.atomic_json(batch/'local-review-worker.json',
                {'pid':1001,'batch':str(batch),'status':'watching','launch_token':'stale'})
            child=Mock(pid=1001,returncode=1)
            child.poll.side_effect=[None,1]
            with patch.object(local_review.subprocess,'Popen',return_value=child), \
                    patch.object(local_review.time,'sleep'):
                with self.assertRaisesRegex(RuntimeError,'exited'):
                    local_review.start_watch(batch)


if __name__=='__main__':unittest.main()
