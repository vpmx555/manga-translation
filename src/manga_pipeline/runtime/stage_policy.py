"""Per-request limits on one shared vLLM server."""


def is_analyze(task):
    return task.startswith('analyze_') and not task.startswith('analyze_scene')


def request_policy(config, task):
    llm = config.get('llm', config)
    analyze = is_analyze(task)
    thinking = False
    return {'enable_thinking': thinking,
            'num_ctx': llm.get('analyze_num_ctx', llm['num_ctx']) if analyze else llm['num_ctx'],
            'timeout': (llm.get('graph_timeout', 1200) if task == 'graph_chapter_v7' else
                        llm.get('analyze_timeout', llm['timeout']) if analyze else llm['timeout']),
            'temperature': llm.get('analyze_temperature', llm.get('temperature', 0)) if thinking else llm.get('temperature', 0),
            'presence_penalty': llm.get('analyze_presence_penalty', llm.get('presence_penalty', 0)) if thinking else llm.get('presence_penalty', 0)}


def server_context(config):
    llm = config.get('llm', config)
    return max(llm['num_ctx'], llm.get('analyze_num_ctx', llm['num_ctx']))
