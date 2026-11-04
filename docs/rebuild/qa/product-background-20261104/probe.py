"""Finite, synthetic background-question checks. Importing starts nothing.

Only answeronce may mutate: one admitted canonical question response, with an
exclusive durable intent, exact current context and a fresh external CUA record.
A timeout or uncertain response never permits replay.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import common

ROOT = Path(__file__).parent
COMMIT = '1b0a7ead49f2659ef536d47947a9ed7364d0e632'
LIMIT, OUTPUT = 262144, 65536
READ_METHODS = {'sessions.list', 'sessions.messages', 'runs.get', 'product.snapshot'}
QUESTION = {'id': 'background_gate', 'header': 'Continuar',
    'prompt': '¿Continuamos la prueba sintética?', 'options': [
        {'id': 'continuar', 'label': 'Continuar', 'description': 'Completa esta prueba.'},
        {'id': 'detener', 'label': 'Detener', 'description': 'Termina sin continuar.'}],
    'allow_freeform': True}
A_PREFERENCES = {'revision': 2, 'enabled': True, 'silenced': True}
B_PREFERENCES = {'revision': 0, 'enabled': False, 'silenced': False}


def require(condition, code):
    common.require(condition, code)


def identifier(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{32}', value), 'public_id_invalid')
    return value


def positive(value):
    return type(value) is int and 0 < value < 2**52


def integer(value):
    require(type(value) is int and 0 <= value < 2**52, 'revision_invalid')
    return value


def encode(value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False,
                     separators=(',', ':')).encode() + b'\n'
    require(len(raw) <= OUTPUT, 'sanitized_output_limit')
    return raw


def snapshot(value, session, run):
    scope = value['scope']
    require(scope['session_id'] == session and scope['temporary'] is False
            and scope['run_id'] == run, 'snapshot_scope_changed')
    preferences = value['notification_preferences']
    require(type(preferences['enabled']) is bool and type(preferences['silenced']) is bool,
            'invalid_preferences')
    rows = value['interactions']
    require(type(rows) is list and len(rows) <= 512, 'interaction_limit')
    questions, notices, other = [], 0, []
    for row in rows:
        if row['kind'] == 'notification':
            notices += 1
        if row['kind'] != 'question':
            other.append(row['kind'])
            continue
        payload = row['payload']
        require(payload.get('questions') == [QUESTION]
                and payload.get('grants_tool_approval') is False, 'noncanonical_question')
        require(row['status'] in {'pending', 'answered', 'consumed', 'cancelled', 'expired'},
                'invalid_question_status')
        require(type(row.get('answers_available')) is bool, 'answer_availability_invalid')
        questions.append({'id': identifier(row['id']), 'revision': integer(row['revision']),
            'status': row['status'], 'answers_available': row['answers_available'],
            'canonical_question': True, 'grants_tool_approval': False})
    return {'scope': {'session_id': session, 'run_id': run, 'temporary': False,
                     'workspace_id': scope['workspace_id'], 'agent_id': scope['agent_id'],
                     'generation': integer(scope['generation'])},
        'notification_preferences': {'revision': integer(preferences['revision']),
                                    'enabled': preferences['enabled'],
                                    'silenced': preferences['silenced']},
        'questions': questions, 'notification_count': notices,
        'other_interaction_kinds': sorted(other)}


class Inspector:
    def __init__(self, client, args, checkpoint):
        self.client, self.args, self.checkpoint = client, args, checkpoint
        self.received_bytes = 0

    async def call(self, method, params):
        require(method in READ_METHODS, 'mutation_not_admitted')
        self.checkpoint()
        value = await self.client.call(method, params)
        self.received_bytes += len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode())
        require(self.received_bytes <= LIMIT, 'aggregate_rpc_input_limit')
        self.checkpoint()
        return value

    async def inspect(self, waiting=None):
        listing = await self.call('sessions.list', {'temporary': False})
        sessions = listing['sessions']
        require(not listing['has_more'] and type(sessions) is list and len(sessions) <= 2,
                'fresh_profile_session_limit')
        inventory, histories = [], {}
        for row in sessions:
            session = identifier(row['session_id'])
            require(row['temporary'] is False and session not in histories,
                    'unexpected_or_duplicate_session')
            history = await self.call('sessions.messages',
                {'session_id': session, 'temporary': False, 'after': 0})
            require(not history['has_more'] and not history['runs_has_more']
                    and len(history['messages']) <= 2 and len(history['runs']) <= 1,
                    'fresh_history_limit')
            histories[session] = history
            inventory.append({'session_id': session, 'message_count': len(history['messages']),
                'runs': [{'run_id': identifier(run['run_id']), 'status': run['status'],
                          'outcome': run['outcome']} for run in history['runs']]})
        result = {'inventory': inventory, 'received_bytes': self.received_bytes}
        if self.args.mode == 'inventory':
            return {**result, 'status': 'inspected', 'conditions': {}}
        a, b, run = self.args.session_a, self.args.session_b, self.args.run_id
        require(set(histories) == {a, b} and a != b, 'exact_two_sessions_required')
        snapshots = {}
        for label, session in (('a_host', a), ('b_host', b)):
            snapshots[label] = snapshot(await self.call('product.snapshot',
                {'session_id': session, 'temporary': False, 'run_id': 'host'}), session, 'host')
        conditions = {
            'b_empty': not histories[b]['messages'] and not histories[b]['runs'],
            'b_default_preferences': snapshots['b_host']['notification_preferences'] == B_PREFERENCES,
            'a_exact_enabled_silenced': snapshots['a_host']['notification_preferences'] == A_PREFERENCES,
            'no_OS_notices': all(v['notification_count'] == 0 for v in snapshots.values()),
            'no_other_interactions': all(not v['other_interaction_kinds'] for v in snapshots.values()),
            'b_no_questions': not snapshots['b_host']['questions'],
        }
        if self.args.mode == 'before':
            conditions.update(a_empty=not histories[a]['messages'] and not histories[a]['runs'],
                              a_no_question_yet=not snapshots['a_host']['questions'])
        else:
            row = (await self.call('runs.get',
                {'session_id': a, 'temporary': False, 'run_id': run}))['run']
            require(row['run_id'] == run, 'selected_run_changed')
            snapshots['a_run'] = snapshot(await self.call('product.snapshot',
                {'session_id': a, 'temporary': False, 'run_id': run}), a, run)
            receipts = row['action_receipts']
            require(type(receipts) is list and len(receipts) <= 4, 'tool_receipt_limit')
            questions = snapshots['a_run']['questions']
            conditions.update(
                exactly_one_run=len(histories[a]['runs']) == 1
                    and histories[a]['runs'][0]['run_id'] == run,
                exactly_one_question=len(questions) == 1,
                no_other_tools=all(r['tool_name'] == 'request_user_input' for r in receipts),
                no_OS_notices=all(v['notification_count'] == 0 for v in snapshots.values()),
                no_other_interactions=all(not v['other_interaction_kinds'] for v in snapshots.values()),
                same_A_preferences_in_run=snapshots['a_run']['notification_preferences'] == A_PREFERENCES,
            )
            result['question'] = questions[0] if len(questions) == 1 else None
            result['run'] = {'run_id': run, 'status': row['status'], 'outcome': row['outcome'],
                            'cleanup_confirmed': row['cleanup_confirmed'],
                            'answer_source': row.get('answer_source'),
                            'action_receipts_count': len(receipts),
                            'answer_bytes': len((row.get('answer') or '').encode())}
            if self.args.mode == 'waiting':
                conditions.update(
                    run_waiting_for_question=row['status'] == 'running'
                        and row['cleanup_confirmed'] is False,
                    question_pending=len(questions) == 1 and questions[0]['status'] == 'pending'
                        and questions[0]['revision'] == 1 and not questions[0]['answers_available'],
                    no_settled_tool_before_answer=not receipts,
                )
            else:
                require(waiting is not None and waiting['status'] == 'passed_selected_conditions'
                        and waiting['mode'] == 'waiting' and waiting['session_a'] == a
                        and waiting['session_b'] == b and waiting['run_id'] == run,
                        'waiting_baseline_not_current')
                old = waiting['question']
                conditions.update(
                    run_completed=row['status'] == 'completed' and row['outcome'] == 'known'
                        and row['cleanup_confirmed'] is True and row.get('answer_source') == 'model',
                    question_consumed_once=len(questions) == 1 and questions[0]['id'] == old['id']
                        and questions[0]['status'] == 'consumed'
                        and questions[0]['revision'] == old['revision'] + 2
                        and not questions[0]['answers_available'],
                    one_known_question_tool=len(receipts) == 1 and receipts[0]['succeeded'] is True
                        and receipts[0]['verified'] is True and receipts[0]['effect_status'] == 'known',
                )
                if len(receipts) == 1:
                    receipt = receipts[0]
                    output = receipt.get('output', '')
                    require(type(output) is str and len(output.encode()) <= 512
                            and not receipt.get('output_truncated'), 'question_receipt_output_limit')
                    expected = {'question_id': old['id'], 'status': 'answered',
                                'answers_stored': False, 'grants_tool_approval': False}
                    conditions['receipt_no_answer_values_or_authority'] = json.loads(output) == expected
        return {**result, 'snapshots': snapshots, 'conditions': conditions,
            'received_bytes': self.received_bytes,
            'status': 'passed_selected_conditions' if all(conditions.values()) else 'failed_conditions'}


def load_attempt(pin):
    prepared = common.read_json(ROOT / 'prepared.json', pin=pin)
    require(prepared['commit'] == COMMIT and positive(prepared['run_id'])
            and positive(prepared['artifact_id']) and prepared['full_ci_passed'] is True,
            'exact_ci_preparation_required')
    admission = common.read_json(ROOT / 'admission.json', pin=prepared['admission_sha256'])
    require(admission['harness_root'] == str(ROOT)
            and admission['scripts_sha256']['probe.py'] == common.sha(Path(__file__)),
            'probe_admission_changed')
    for name, digest in admission['scripts_sha256'].items():
        require(common.sha(ROOT / name) == digest, 'harness_source_changed')
    require(common.sha(ROOT / 'reviewer-checklist.json') == admission['reviewer_checklist_sha256'],
            'review_checklist_changed')
    active = common.read_json(ROOT / 'active.json')
    require(active['prepared_sha256'] == pin
            and type(active['launch_requested_unix']) in {int, float}
            and math.isfinite(active['launch_requested_unix'])
            and active['launch_requested_unix'] <= time.time()
            and time.time() < active['launch_requested_unix'] + 810
            and not (ROOT / 'quit-intent.json').exists()
            and not (ROOT / 'closed.json').exists(), 'current_native_attempt_required')
    current = common.snapshot_pid(active['app']['pid'])
    require(current is not None and common.identity(current) == common.identity(active['app']),
            'active_app_identity_changed')
    quota, state, app = map(Path, (prepared['root_quota'], prepared['state'], prepared['app']))
    require(quota.parent == Path('/private/tmp')
            and re.fullmatch('jq-[A-Za-z0-9_-]{1,64}', quota.name)
            and app == quota / 'Jarvis.app' and state == quota / 'state',
            'pinned_quota_profile_required')
    core = app / 'Contents/Resources/JarvisRuntime/core'
    key = quota / 'state.clients/desktop.key'
    common.private(quota, kind='directory')
    common.private(state, kind='directory')
    common.private(core, kind='directory')
    common.private(core.parent / 'site-packages', kind='directory')
    common.private(key, kind='file')
    common.read_json(core.parent / 'runtime-manifest.json', pin=prepared['manifest_sha256'],
                     maximum=8 * 1024 * 1024)
    return prepared, admission, active, state, key, core


def client_for(state, key, core):
    sys.path[:0] = [str(core), str(core.parent / 'site-packages')]
    from jarvis_core.client_identity import ClientIdentity
    from jarvis_core.protocol import LocalRpcClient
    for module in ('jarvis_core', 'jarvis_core.client_identity', 'jarvis_core.protocol'):
        require(Path(sys.modules[module].__file__).resolve().parent == core / 'jarvis_core',
                'bundled_runtime_import_changed')
    return LocalRpcClient(state, identity=ClientIdentity.load(key), timeout=5,
                          max_frame_bytes=LIMIT)


def check_attestation(ui, args, waiting, prepared, active):
    require(ui.get('schema') == 'jarvis.background-question-root-cua-attestation/1'
            and ui.get('source') == 'root_cua_public_accessibility'
            and ui.get('prepared_sha256') == args.prepared_sha256
            and ui.get('waiting_sha256') == args.waiting_sha256
            and ui.get('selected_session_id') == waiting['session_b']
            and type(ui.get('enabled_AX_value')) is int and ui['enabled_AX_value'] == 0
            and ui.get('a_pending_question') is True
            and ui.get('app_pid') == active['app']['pid']
            and ui.get('bundle_identifier') == prepared['bundle_id'],
            'fresh_root_CUA_attestation_required')
    observed, reference = ui.get('observed_at_unix'), ui.get('evidence_reference')
    require(type(observed) in {int, float} and math.isfinite(observed)
            and waiting['observed_at_unix'] <= observed <= time.time()
            and time.time() - observed <= 60
            and type(reference) is str and reference.isascii() and 1 <= len(reference) <= 256
            and all(32 <= ord(char) < 127 for char in reference), 'CUA_attestation_time_invalid')


async def answer_once(args, context, checkpoint, waiting, ui, mutable):
    prepared, _admission, active, state, key, core = context
    client = client_for(state, key, core)
    try:
        async with asyncio.timeout(30):
            await client.connect()
            selected = SimpleNamespace(mode='waiting', session_a=waiting['session_a'],
                                       session_b=waiting['session_b'], run_id=waiting['run_id'])
            fresh = await Inspector(client, selected, checkpoint).inspect()
            require(fresh['status'] == 'passed_selected_conditions'
                    and fresh['question'] == waiting['question']
                    and fresh['snapshots'] == waiting['snapshots'],
                    'pending_question_or_preferences_changed')
            check_attestation(ui, args, waiting, prepared, active)
            checkpoint()
            question = waiting['question']
            intent = {'schema': 'jarvis.background-question-answer-intent/1',
                'prepared_sha256': args.prepared_sha256,
                'waiting_sha256': args.waiting_sha256,
                'ui_attestation_sha256': args.ui_attestation_sha256,
                'app_pid': active['app']['pid'], 'profile': prepared['state'],
                'session_a': waiting['session_a'], 'session_b': waiting['session_b'],
                'run_id': waiting['run_id'], 'question_record_id': question['id'],
                'question_revision': question['revision'], 'created_at_unix': time.time(),
                'maximum_mutation_calls': 1, 'automatic_retry': False,
                'answer_values_persisted': False, 'grants_tool_approval': False}
            common.write_new(ROOT / 'answer-intent.json', encode(intent))
            mutable['intent_recorded'] = True
            mutable['intent_sha256'] = common.sha(ROOT / 'answer-intent.json')
            checkpoint()
            current = common.snapshot_pid(active['app']['pid'])
            require(current is not None
                    and common.identity(current) == common.identity(active['app']),
                    'app_identity_changed_before_answer')
            check_attestation(ui, args, waiting, prepared, active)
            mutable['answer_call_attempted'] = True
            response = await client.call('product.interaction', {
                'session_id': waiting['session_a'], 'temporary': False,
                'run_id': waiting['run_id'], 'kind': 'question.answer', 'arguments': {
                    'id': question['id'], 'revision': question['revision'],
                    'answers': {QUESTION['id']: {'option_id': 'continuar'}}}})
            mutable['response_received'] = True
            require(len(json.dumps(response, ensure_ascii=True).encode()) <= LIMIT,
                    'answer_ACK_input_limit')
            expected = {'id': question['id'], 'revision': question['revision'] + 1,
                        'status': 'answered', 'grants_tool_approval': False}
            require(response == {'result': expected}, 'answer_ACK_not_exact')
            checkpoint()
            return {'status': 'answer_acknowledged', 'question_record_id': question['id'],
                    'revision_after': expected['revision'], 'grants_tool_approval': False}
    finally:
        await asyncio.wait_for(client.aclose(), 4)


async def read_only(args, context, checkpoint, waiting):
    _prepared, _admission, _active, state, key, core = context
    client = client_for(state, key, core)
    try:
        async with asyncio.timeout(30):
            await client.connect()
            return await Inspector(client, args, checkpoint).inspect(waiting)
    finally:
        await asyncio.wait_for(client.aclose(), 4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('inventory', 'before', 'waiting', 'answeronce', 'after'))
    parser.add_argument('--prepared-sha256', required=True)
    parser.add_argument('--session-a')
    parser.add_argument('--session-b')
    parser.add_argument('--run-id')
    parser.add_argument('--waiting-sha256')
    parser.add_argument('--ui-attestation-sha256')
    parser.add_argument('--admit-one-answer', action='store_true')
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    require(sys.flags.no_user_site == 1 and sys.dont_write_bytecode, 'use_python_B_s')
    require(re.fullmatch('[a-f0-9]{64}', args.prepared_sha256), 'prepared_pin_required')
    require(args.receipt.is_absolute() and args.receipt.parent == ROOT
            and re.fullmatch(r'[a-z0-9-]{1,64}\.json', args.receipt.name)
            and not args.receipt.exists(), 'new_explicit_receipt_required')
    if args.mode not in {'inventory', 'answeronce'}:
        identifier(args.session_a)
        identifier(args.session_b)
    if args.mode in {'waiting', 'after'}:
        identifier(args.run_id)
    if args.mode in {'after', 'answeronce'}:
        require(re.fullmatch('[a-f0-9]{64}', args.waiting_sha256 or ''), 'waiting_pin_required')
    if args.mode == 'waiting':
        require(args.receipt == ROOT / 'waiting.json', 'fixed_waiting_receipt_required')
    if args.mode == 'answeronce':
        require(args.session_a is None and args.session_b is None and args.run_id is None,
                'answer_context_only_from_pinned_waiting_receipt')
        require(args.admit_one_answer and args.receipt == ROOT / 'answer.json'
                and re.fullmatch('[a-f0-9]{64}', args.ui_attestation_sha256 or '')
                and not (ROOT / 'answer-intent.json').exists(), 'explicit_new_one_answer_required')
    elif args.admit_one_answer:
        require(False, 'answer_flag_only_for_answeronce')
    started, deadline = time.monotonic(), time.monotonic() + 40
    previous_alarm = signal.getsignal(signal.SIGALRM)
    mutable = {'intent_recorded': False, 'answer_call_attempted': False,
               'response_received': False}

    def expire(_number, _frame):
        raise common.GuardIssue('probe_deadline')

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, 40)
    context, result = None, None
    try:
        context = load_attempt(args.prepared_sha256)
        prepared = context[0]

        def checkpoint():
            require(time.monotonic() < deadline, 'probe_deadline')
            common.resources(prepared)

        checkpoint()
        waiting = None
        if args.mode in {'after', 'answeronce'}:
            waiting = common.read_json(ROOT / 'waiting.json', pin=args.waiting_sha256)
            require(waiting['mode'] == 'waiting'
                    and waiting['status'] == 'passed_selected_conditions'
                    and waiting['prepared_sha256'] == args.prepared_sha256
                    and waiting['source_commit'] == COMMIT
                    and waiting['ci_run_id'] == prepared['run_id']
                    and all(waiting['conditions'].values()), 'exact_waiting_baseline_required')
            identifier(waiting['session_a'])
            identifier(waiting['session_b'])
            identifier(waiting['run_id'])
        if args.mode == 'answeronce':
            ui = common.read_json(ROOT / 'ui-before-answer.json', pin=args.ui_attestation_sha256)
            check_attestation(ui, args, waiting, prepared, context[2])
            result = asyncio.run(answer_once(args, context, checkpoint, waiting, ui, mutable))
        else:
            result = asyncio.run(read_only(args, context, checkpoint, waiting))
        checkpoint()
    except (Exception, KeyboardInterrupt) as error:
        result = {'status': 'unknown_preserved_no_replay' if mutable['answer_call_attempted']
                  else 'not_sent_preserved' if args.mode == 'answeronce' else 'failed_or_unknown',
                  'error': str(error) if isinstance(error, common.GuardIssue) else type(error).__name__}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_alarm)
    result.update(schema='jarvis.background-question-probe/1', mode=args.mode,
        source_commit=COMMIT, ci_run_id=context[0]['run_id'] if context else None,
        prepared_sha256=args.prepared_sha256,
        session_a=args.session_a, session_b=args.session_b, run_id=args.run_id,
        waiting_sha256=args.waiting_sha256,
        elapsed_seconds=round(time.monotonic() - started, 3), observed_at_unix=time.time(),
        mutations_sent=mutable['answer_call_attempted'], **mutable,
        provider_auth_read=False, model_or_answer_values_emitted=False,
        answer_values_persisted=False, ui_selection_proven_by_RPC=False,
        full_qualification=False, permits_effect_replay=False)
    common.write_new(args.receipt, encode(result))
    print(json.dumps({'status': result['status'], 'receipt': str(args.receipt),
                      'sha256': common.sha(args.receipt), 'conditions': result.get('conditions', {}),
                      'permits_effect_replay': False}))
    return 0 if result['status'] in {'inspected', 'passed_selected_conditions',
                                   'answer_acknowledged'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
