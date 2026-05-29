"""Unit tests for checker.py"""

import tempfile
import unittest
from pathlib import Path

from checker import (
    OutputFile,
    Warning,
    strip_line_comment,
    clean_path,
    has_variable,
    is_fd_to_fd,
    tokenize,
    non_flag_tokens,
    split_pipeline,
    remove_bracket_expressions,
    mask_single_quoted,
    extract_redirects,
    extract_command_outputs,
    extract_awk_outputs,
    collect_assignments,
    check_set_flags,
    check_unquoted_vars,
    check_var_before_assign,
    analyze,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def paths(results):
    """Extract just the path strings from a list of OutputFile."""
    return [r.path for r in results]


def messages(results):
    """Extract just the message strings from a list of Warning."""
    return [r.message for r in results]


def linenos(results):
    return [r.lineno for r in results]


# ---------------------------------------------------------------------------
# strip_line_comment
# ---------------------------------------------------------------------------

class TestStripLineComment(unittest.TestCase):

    def test_no_comment(self):
        self.assertEqual(strip_line_comment('echo hello'), 'echo hello')

    def test_trailing_comment(self):
        self.assertEqual(strip_line_comment('echo hello # world'), 'echo hello ')

    def test_comment_only(self):
        self.assertEqual(strip_line_comment('# this is a comment'), '')

    def test_hash_inside_double_quotes(self):
        self.assertEqual(strip_line_comment('echo "hello#world"'), 'echo "hello#world"')

    def test_hash_inside_single_quotes(self):
        self.assertEqual(strip_line_comment("echo 'hello#world'"), "echo 'hello#world'")

    def test_hash_after_quoted_string(self):
        self.assertEqual(strip_line_comment('echo "hi" # comment'), 'echo "hi" ')

    def test_empty_line(self):
        self.assertEqual(strip_line_comment(''), '')

    def test_comment_with_redirect_inside(self):
        # The > inside the comment should not produce a redirect
        self.assertEqual(strip_line_comment('cmd # > file.txt'), 'cmd ')


# ---------------------------------------------------------------------------
# clean_path
# ---------------------------------------------------------------------------

class TestCleanPath(unittest.TestCase):

    def test_unquoted(self):
        self.assertEqual(clean_path('file.txt'), 'file.txt')

    def test_double_quoted(self):
        self.assertEqual(clean_path('"file.txt"'), 'file.txt')

    def test_single_quoted(self):
        self.assertEqual(clean_path("'file.txt'"), 'file.txt')

    def test_quoted_with_spaces(self):
        self.assertEqual(clean_path('"my file.txt"'), 'my file.txt')

    def test_mismatched_quotes(self):
        # Mismatched quotes are not stripped
        self.assertEqual(clean_path('"file.txt\''), '"file.txt\'')

    def test_short_string(self):
        self.assertEqual(clean_path('x'), 'x')

    def test_empty_string(self):
        self.assertEqual(clean_path(''), '')


# ---------------------------------------------------------------------------
# has_variable
# ---------------------------------------------------------------------------

class TestHasVariable(unittest.TestCase):

    def test_no_variable(self):
        self.assertFalse(has_variable('/tmp/file.txt'))

    def test_bare_variable(self):
        self.assertTrue(has_variable('$OUTPUT'))

    def test_braced_variable(self):
        self.assertTrue(has_variable('${OUTPUT_DIR}/file.txt'))

    def test_variable_in_path(self):
        self.assertTrue(has_variable('/tmp/$USER/file.txt'))


# ---------------------------------------------------------------------------
# is_fd_to_fd
# ---------------------------------------------------------------------------

class TestIsFdToFd(unittest.TestCase):

    def test_fd_1(self):
        self.assertTrue(is_fd_to_fd('&1'))

    def test_fd_2(self):
        self.assertTrue(is_fd_to_fd('&2'))

    def test_fd_close(self):
        self.assertTrue(is_fd_to_fd('&-'))

    def test_fd_digit(self):
        self.assertTrue(is_fd_to_fd('&9'))

    def test_regular_file(self):
        self.assertFalse(is_fd_to_fd('out.txt'))

    def test_variable_path(self):
        self.assertFalse(is_fd_to_fd('$OUTPUT'))

    def test_dev_null(self):
        self.assertFalse(is_fd_to_fd('/dev/null'))

    def test_leading_space(self):
        self.assertTrue(is_fd_to_fd(' &1'))


# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------

class TestTokenize(unittest.TestCase):

    def test_simple(self):
        self.assertEqual(tokenize('cp src dest'), ['cp', 'src', 'dest'])

    def test_double_quoted_with_space(self):
        self.assertEqual(tokenize('cp "my src" dest'), ['cp', '"my src"', 'dest'])

    def test_single_quoted_with_space(self):
        self.assertEqual(tokenize("cp 'my src' dest"), ['cp', "'my src'", 'dest'])

    def test_extra_spaces(self):
        self.assertEqual(tokenize('cp  src   dest'), ['cp', 'src', 'dest'])

    def test_tab_separator(self):
        self.assertEqual(tokenize('cp\tsrc\tdest'), ['cp', 'src', 'dest'])

    def test_empty_string(self):
        self.assertEqual(tokenize(''), [])

    def test_single_token(self):
        self.assertEqual(tokenize('touch'), ['touch'])


# ---------------------------------------------------------------------------
# non_flag_tokens
# ---------------------------------------------------------------------------

class TestNonFlagTokens(unittest.TestCase):

    def test_filters_flags(self):
        self.assertEqual(non_flag_tokens(['-p', 'dir']), ['dir'])

    def test_keeps_positional(self):
        self.assertEqual(non_flag_tokens(['src', 'dest']), ['src', 'dest'])

    def test_double_dash_flag(self):
        self.assertEqual(non_flag_tokens(['--recursive', 'src', 'dest']), ['src', 'dest'])

    def test_empty(self):
        self.assertEqual(non_flag_tokens([]), [])

    def test_all_flags(self):
        self.assertEqual(non_flag_tokens(['-r', '-f', '-v']), [])


# ---------------------------------------------------------------------------
# split_pipeline
# ---------------------------------------------------------------------------

class TestSplitPipeline(unittest.TestCase):

    def test_single_command(self):
        self.assertEqual(split_pipeline('echo hello'), ['echo hello'])

    def test_pipe(self):
        self.assertEqual(split_pipeline('cmd | tee out.log'), ['cmd', 'tee out.log'])

    def test_semicolon(self):
        self.assertEqual(split_pipeline('cmd1; cmd2'), ['cmd1', 'cmd2'])

    def test_and_and(self):
        self.assertEqual(split_pipeline('cmd1 && cmd2'), ['cmd1', 'cmd2'])

    def test_or_or(self):
        self.assertEqual(split_pipeline('cmd1 || cmd2'), ['cmd1', 'cmd2'])

    def test_pipe_and(self):
        parts = split_pipeline('cmd1 | tee f.txt && echo done')
        self.assertIn('tee f.txt', parts)

    def test_empty(self):
        self.assertEqual(split_pipeline(''), [])


# ---------------------------------------------------------------------------
# remove_bracket_expressions
# ---------------------------------------------------------------------------

class TestRemoveBracketExpressions(unittest.TestCase):

    def test_double_bracket(self):
        result = remove_bracket_expressions('[[ $a > $b ]]')
        self.assertNotIn('>', result)

    def test_single_bracket(self):
        result = remove_bracket_expressions('[ $a -gt 0 ]')
        self.assertNotIn('-gt', result)

    def test_no_brackets(self):
        self.assertEqual(remove_bracket_expressions('echo hello'), 'echo hello')

    def test_keeps_redirect_outside(self):
        result = remove_bracket_expressions('[[ $a > $b ]] && cmd > out.txt')
        self.assertIn('>', result)
        self.assertIn('out.txt', result)


# ---------------------------------------------------------------------------
# mask_single_quoted
# ---------------------------------------------------------------------------

class TestMaskSingleQuoted(unittest.TestCase):

    def test_masks_content(self):
        result = mask_single_quoted("awk '{print > \"file\"}'")
        self.assertNotIn('>', result.split("'")[1])

    def test_preserves_quotes(self):
        result = mask_single_quoted("echo 'hello'")
        self.assertTrue(result.startswith("echo '"))
        self.assertTrue(result.endswith("'"))

    def test_no_single_quotes(self):
        line = 'echo hello > out.txt'
        self.assertEqual(mask_single_quoted(line), line)

    def test_multiple_quoted_regions(self):
        result = mask_single_quoted("echo 'a>b' 'c>d'")
        # Both > chars inside quotes should be masked
        parts = result.split("'")
        # parts[1] and parts[3] are the masked interiors
        self.assertNotIn('>', parts[1])
        self.assertNotIn('>', parts[3])


# ---------------------------------------------------------------------------
# extract_redirects
# ---------------------------------------------------------------------------

class TestExtractRedirects(unittest.TestCase):

    def _paths(self, line):
        return paths(extract_redirects(line, 1))

    # Basic redirects
    def test_stdout_truncate(self):
        self.assertIn('out.txt', self._paths('echo hello > out.txt'))

    def test_stdout_append(self):
        self.assertIn('out.txt', self._paths('echo hello >> out.txt'))

    def test_combined_truncate(self):
        self.assertIn('combined.log', self._paths('cmd &> combined.log'))

    def test_combined_append(self):
        self.assertIn('combined.log', self._paths('cmd &>> combined.log'))

    def test_stderr_redirect(self):
        self.assertIn('error.log', self._paths('cmd 2> error.log'))

    def test_stderr_append(self):
        self.assertIn('error.log', self._paths('cmd 2>> error.log'))

    def test_numbered_fd(self):
        self.assertIn('persistent.log', self._paths('exec 3> persistent.log'))

    def test_clobber(self):
        self.assertIn('out.txt', self._paths('cmd >| out.txt'))

    def test_read_write(self):
        self.assertIn('rw.txt', self._paths('exec 5<> rw.txt'))

    def test_quoted_path(self):
        self.assertIn('my file.txt', self._paths('cmd > "my file.txt"'))

    def test_variable_path(self):
        result = extract_redirects('cmd > $OUTPUT', 1)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].has_variable)

    def test_fd_to_fd_skipped(self):
        self.assertEqual(self._paths('cmd 2>&1'), [])

    def test_fd_to_stderr_skipped(self):
        self.assertEqual(self._paths('cmd >&2'), [])

    def test_fd_close_skipped(self):
        self.assertEqual(self._paths('exec 3>&-'), [])

    def test_comment_ignored(self):
        # redirect in comment should not be detected
        self.assertEqual(self._paths('cmd # > file.txt'), [])

    def test_bracket_comparison_ignored(self):
        self.assertEqual(self._paths('[[ $a > $b ]]'), [])

    def test_awk_literal_ignored(self):
        # > inside single-quoted awk body must not be treated as a redirect
        self.assertEqual(self._paths("awk '{print > \"out.csv\"}' data"), [])

    def test_lineno_stored(self):
        result = extract_redirects('cmd > out.txt', 42)
        self.assertEqual(result[0].lineno, 42)

    def test_multiple_redirects_on_line(self):
        result = extract_redirects('cmd > out.txt 2> err.txt', 1)
        ps = paths(result)
        self.assertIn('out.txt', ps)
        self.assertIn('err.txt', ps)


# ---------------------------------------------------------------------------
# extract_command_outputs — redirect-free commands
# ---------------------------------------------------------------------------

class TestExtractCommandOutputs(unittest.TestCase):

    def _paths(self, line):
        return paths(extract_command_outputs(line, 1))

    # touch
    def test_touch_single(self):
        self.assertIn('app.log', self._paths('touch app.log'))

    def test_touch_multiple(self):
        ps = self._paths('touch a.txt b.txt')
        self.assertIn('a.txt', ps)
        self.assertIn('b.txt', ps)

    def test_touch_with_flag(self):
        ps = self._paths('touch -t 202001010000 foo.txt')
        self.assertIn('foo.txt', ps)
        self.assertNotIn('-t', ps)

    # mkdir
    def test_mkdir(self):
        self.assertIn('build/out', self._paths('mkdir -p build/out'))

    # cp
    def test_cp(self):
        self.assertIn('dest.sh', self._paths('cp src.sh dest.sh'))

    def test_cp_recursive(self):
        self.assertIn('dest/', self._paths('cp -r src/ dest/'))

    def test_cp_only_one_arg_ignored(self):
        # cp with only one positional arg should be skipped (no destination)
        self.assertEqual(self._paths('cp src.sh'), [])

    # mv
    def test_mv(self):
        self.assertIn('final.sh', self._paths('mv tmp.sh final.sh'))

    # ln
    def test_ln(self):
        self.assertIn('./python', self._paths('ln -s /usr/bin/python3 ./python'))

    # install
    def test_install(self):
        self.assertIn('dest', self._paths('install -D src dest'))

    # tee — in pipeline
    def test_tee_in_pipeline(self):
        self.assertIn('out.log', self._paths('some_command | tee out.log'))

    def test_tee_multiple_files(self):
        ps = self._paths('cmd | tee a.log b.log')
        self.assertIn('a.log', ps)
        self.assertIn('b.log', ps)

    def test_tee_append(self):
        self.assertIn('out.log', self._paths('cmd | tee -a out.log'))

    # sponge
    def test_sponge(self):
        self.assertIn('result.txt', self._paths('cmd | sponge result.txt'))

    # dd
    def test_dd(self):
        self.assertIn('disk.img', self._paths('dd if=/dev/zero of=disk.img bs=1M count=10'))

    def test_dd_no_of(self):
        self.assertEqual(self._paths('dd if=/dev/zero bs=1M'), [])

    # truncate
    def test_truncate(self):
        self.assertIn('empty.txt', self._paths('truncate -s 0 empty.txt'))

    # mkfifo
    def test_mkfifo(self):
        self.assertIn('/tmp/my_pipe', self._paths('mkfifo /tmp/my_pipe'))

    # mktemp — bare command
    def test_mktemp_bare(self):
        self.assertIn('<mktemp>', self._paths('mktemp'))

    # mktemp — inside $()
    def test_mktemp_subshell(self):
        self.assertIn('<mktemp>', self._paths('TMPFILE=$(mktemp)'))

    # curl
    def test_curl_o(self):
        self.assertIn('file.tar.gz', self._paths('curl -o file.tar.gz https://example.com/f'))

    def test_curl_long_output(self):
        self.assertIn('file.tar.gz', self._paths('curl --output file.tar.gz https://x.com/f'))

    def test_curl_o_attached(self):
        self.assertIn('file.tar.gz', self._paths('curl -ofile.tar.gz https://x.com/f'))

    def test_curl_big_O(self):
        self.assertIn('<remote filename>', self._paths('curl -O https://x.com/f'))

    # wget
    def test_wget_O(self):
        self.assertIn('out.html', self._paths('wget -O out.html https://example.com/'))

    def test_wget_long_output(self):
        self.assertIn('out.html', self._paths('wget --output-document out.html https://x.com/'))

    def test_wget_O_attached(self):
        self.assertIn('out.html', self._paths('wget -Oout.html https://x.com/'))

    # sed -i
    def test_sed_inplace(self):
        self.assertIn('config.txt', self._paths("sed -i 's/foo/bar/' config.txt"))

    def test_sed_inplace_with_backup(self):
        self.assertIn('config.txt', self._paths("sed -i.bak 's/foo/bar/' config.txt"))

    def test_sed_without_inplace(self):
        # sed without -i does not write a file
        self.assertEqual(self._paths("sed 's/foo/bar/' config.txt"), [])

    # openssl
    def test_openssl(self):
        self.assertIn('cert.pem', self._paths('openssl req -new -x509 -out cert.pem'))

    # gpg
    def test_gpg(self):
        self.assertIn('enc.gpg', self._paths('gpg -o enc.gpg -c secret.txt'))

    def test_gpg_long_output(self):
        self.assertIn('enc.gpg', self._paths('gpg --output enc.gpg -c secret.txt'))

    def test_gpg2(self):
        self.assertIn('enc.gpg', self._paths('gpg2 -o enc.gpg -c secret.txt'))

    # Full-path command (e.g. /usr/bin/touch)
    def test_full_path_command(self):
        self.assertIn('foo.txt', self._paths('/usr/bin/touch foo.txt'))

    # Env-var prefix stripped
    def test_env_var_prefix(self):
        self.assertIn('foo.txt', self._paths('FOO=bar touch foo.txt'))

    # Pipeline segment detection
    def test_pipeline_tee_in_multi_stage(self):
        # extract_command_outputs handles command-based outputs (tee here).
        # The > out.gz redirect in the last stage is caught by extract_redirects instead.
        ps = self._paths('cat data | tee out.log | gzip > out.gz')
        self.assertIn('out.log', ps)

    def test_pipeline_redirect_caught_by_extract_redirects(self):
        # Verify that the redirect in the last stage of a pipeline IS caught
        # when using the redirect extractor (complementary to command outputs).
        ps = paths(extract_redirects('cat data | tee out.log | gzip > out.gz', 1))
        self.assertIn('out.gz', ps)


# ---------------------------------------------------------------------------
# extract_awk_outputs
# ---------------------------------------------------------------------------

class TestExtractAwkOutputs(unittest.TestCase):

    def _paths(self, line):
        return paths(extract_awk_outputs(line, 1))

    def test_awk_print_truncate(self):
        self.assertIn('report.csv', self._paths("awk '{print > \"report.csv\"}' data.txt"))

    def test_awk_print_append(self):
        self.assertIn('append.csv', self._paths("awk '{print >> \"append.csv\"}' data.txt"))

    def test_awk_no_redirect(self):
        self.assertEqual(self._paths("awk '{print $1}' data.txt"), [])

    def test_awk_single_quoted_body(self):
        result = extract_awk_outputs("awk '{print > \"out.txt\"}' f", 7)
        self.assertEqual(result[0].lineno, 7)
        self.assertEqual(result[0].path, 'out.txt')

    def test_awk_print_operator_label(self):
        result = extract_awk_outputs("awk '{print >> \"a.csv\"}' f", 1)
        self.assertIn('>>', result[0].description)


# ---------------------------------------------------------------------------
# collect_assignments
# ---------------------------------------------------------------------------

class TestCollectAssignments(unittest.TestCase):

    def test_simple_assignment(self):
        a = collect_assignments(['FOO=bar'])
        self.assertEqual(a['FOO'], 1)

    def test_export_assignment(self):
        a = collect_assignments(['export FOO=bar'])
        self.assertEqual(a['FOO'], 1)

    def test_first_occurrence_wins(self):
        a = collect_assignments(['FOO=first', 'FOO=second'])
        self.assertEqual(a['FOO'], 1)

    def test_multiple_vars(self):
        a = collect_assignments(['A=1', 'B=2'])
        self.assertIn('A', a)
        self.assertIn('B', a)

    def test_lineno_tracking(self):
        a = collect_assignments(['echo hello', 'VAR=value'])
        self.assertEqual(a['VAR'], 2)

    def test_no_false_positive_on_equals_equals(self):
        a = collect_assignments(['[[ $a == $b ]]'])
        self.assertNotIn('a', a)

    def test_command_substitution(self):
        a = collect_assignments(['RESULT=$(some_cmd)'])
        self.assertEqual(a['RESULT'], 1)


# ---------------------------------------------------------------------------
# check_set_flags
# ---------------------------------------------------------------------------

class TestCheckSetFlags(unittest.TestCase):

    def test_both_missing(self):
        warns = check_set_flags(['echo hello'])
        msgs = messages(warns)
        self.assertTrue(any("'set -e'" in m for m in msgs))
        self.assertTrue(any("'set -u'" in m for m in msgs))

    def test_set_e_present(self):
        warns = check_set_flags(['set -e', 'echo hello'])
        msgs = messages(warns)
        self.assertFalse(any("'set -e'" in m for m in msgs))
        self.assertTrue(any("'set -u'" in m for m in msgs))

    def test_set_u_present(self):
        warns = check_set_flags(['set -u', 'echo hello'])
        msgs = messages(warns)
        self.assertTrue(any("'set -e'" in m for m in msgs))
        self.assertFalse(any("'set -u'" in m for m in msgs))

    def test_set_eu_combined(self):
        warns = check_set_flags(['set -eu'])
        self.assertEqual(len(warns), 0)

    def test_set_eux_combined(self):
        warns = check_set_flags(['set -eux'])
        self.assertEqual(len(warns), 0)

    def test_set_o_errexit(self):
        warns = check_set_flags(['set -o errexit'])
        msgs = messages(warns)
        self.assertFalse(any("'set -e'" in m for m in msgs))

    def test_set_o_nounset(self):
        warns = check_set_flags(['set -o nounset'])
        msgs = messages(warns)
        self.assertFalse(any("'set -u'" in m for m in msgs))

    def test_lineno_is_zero(self):
        warns = check_set_flags(['echo hello'])
        for w in warns:
            self.assertEqual(w.lineno, 0)


# ---------------------------------------------------------------------------
# check_unquoted_vars
# ---------------------------------------------------------------------------

class TestCheckUnquotedVars(unittest.TestCase):

    def test_unquoted_var(self):
        warns = check_unquoted_vars(['echo $FOO'])
        self.assertEqual(len(warns), 1)
        self.assertIn('$FOO', warns[0].message)

    def test_quoted_var_no_warn(self):
        warns = check_unquoted_vars(['echo "$FOO"'])
        self.assertEqual(len(warns), 0)

    def test_single_quoted_var_no_warn(self):
        warns = check_unquoted_vars(["echo '$FOO'"])
        self.assertEqual(len(warns), 0)

    def test_special_var_no_warn(self):
        for var in ['$?', '$#', '$@', '$*', '$!', '$$', '$-']:
            with self.subTest(var=var):
                warns = check_unquoted_vars([f'echo {var}'])
                self.assertEqual(len(warns), 0, f'False positive for {var}')

    def test_braced_var(self):
        warns = check_unquoted_vars(['echo ${FOO}'])
        self.assertEqual(len(warns), 1)

    def test_one_warning_per_line(self):
        # Multiple unquoted vars on one line → only one warning
        warns = check_unquoted_vars(['cp $SRC $DEST'])
        self.assertEqual(len(warns), 1)

    def test_lineno_correct(self):
        warns = check_unquoted_vars(['echo ok', 'echo $VAR'])
        self.assertEqual(warns[0].lineno, 2)

    def test_comment_not_scanned(self):
        warns = check_unquoted_vars(['echo ok # $FOO'])
        self.assertEqual(len(warns), 0)


# ---------------------------------------------------------------------------
# check_var_before_assign
# ---------------------------------------------------------------------------

class TestCheckVarBeforeAssign(unittest.TestCase):

    def test_use_before_assign(self):
        lines = ['echo $LATE', 'LATE=value']
        assigns = collect_assignments(lines)
        warns = check_var_before_assign(lines, assigns)
        self.assertEqual(len(warns), 1)
        self.assertIn('LATE', warns[0].message)
        self.assertEqual(warns[0].lineno, 1)

    def test_use_after_assign_no_warn(self):
        lines = ['EARLY=value', 'echo $EARLY']
        assigns = collect_assignments(lines)
        warns = check_var_before_assign(lines, assigns)
        self.assertEqual(len(warns), 0)

    def test_unknown_var_no_warn(self):
        # Variable never assigned — not reported (may be from environment)
        lines = ['echo $UNSET_VAR']
        assigns = collect_assignments(lines)
        warns = check_var_before_assign(lines, assigns)
        self.assertEqual(len(warns), 0)

    def test_assignment_line_message(self):
        lines = ['echo $X', 'echo $X', 'X=val']
        assigns = collect_assignments(lines)
        warns = check_var_before_assign(lines, assigns)
        self.assertTrue(all('line 3' in w.message for w in warns))

    def test_no_duplicate_same_var_same_line(self):
        # $X appears twice on line 1 — only one warning
        lines = ['cp $X $X', 'X=val']
        assigns = collect_assignments(lines)
        warns = check_var_before_assign(lines, assigns)
        self.assertEqual(len(warns), 1)

    def test_special_vars_ignored(self):
        lines = ['echo $?', 'echo $#']
        assigns = collect_assignments(lines)
        warns = check_var_before_assign(lines, assigns)
        self.assertEqual(len(warns), 0)


# ---------------------------------------------------------------------------
# analyze — integration tests (uses real temp files)
# ---------------------------------------------------------------------------

class TestAnalyze(unittest.TestCase):

    def _write(self, content: str) -> Path:
        """Write content to a temp file and return its Path."""
        f = tempfile.NamedTemporaryFile(
            mode='w', suffix='.sh', delete=False, encoding='utf-8'
        )
        f.write(content)
        f.close()
        return Path(f.name)

    def test_clean_script_returns_zero(self):
        p = self._write('#!/bin/bash\nset -eu\necho "hello"\n')
        self.assertEqual(analyze(p), 0)

    def test_script_with_output_files_returns_one(self):
        p = self._write('#!/bin/bash\nset -eu\necho hi > out.txt\n')
        self.assertEqual(analyze(p), 1)

    def test_script_with_warnings_returns_one(self):
        # No set -e → warning → exit 1
        p = self._write('#!/bin/bash\necho hello\n')
        self.assertEqual(analyze(p), 1)

    def test_missing_file_returns_two(self):
        self.assertEqual(analyze(Path('/nonexistent/path/script.sh')), 2)

    def test_all_patterns_detected(self):
        script = '\n'.join([
            '#!/bin/bash',
            'set -eu',
            'echo hi > out.txt',
            'touch marker.lock',
            'cp src dst',
            'dd if=/dev/zero of=disk.img bs=1k count=1',
            "awk '{print > \"rep.csv\"}' data",
        ])
        p = self._write(script)
        # Just verify it runs without exception and returns 1 (output files present)
        self.assertEqual(analyze(p), 1)

    def test_set_eu_no_flag_warnings(self):
        p = self._write('#!/bin/bash\nset -eu\necho "hello"\n')
        # No warnings about set -e or set -u
        result = analyze(p)
        self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# Edge cases and regression tests
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_redirect_after_awk_not_double_reported(self):
        # awk line should give awk print > result, not also a raw > redirect
        results = extract_awk_outputs("awk '{print > \"out.csv\"}' f", 1)
        redirect_results = extract_redirects("awk '{print > \"out.csv\"}' f", 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(redirect_results), 0)

    def test_heredoc_redirect_detected(self):
        # cat > file << EOF: the > redirect is on the same line
        ps = paths(extract_redirects('cat > heredoc.txt << EOF', 1))
        self.assertIn('heredoc.txt', ps)

    def test_exec_fd_redirect(self):
        ps = paths(extract_redirects('exec 3> log.txt', 1))
        self.assertIn('log.txt', ps)

    def test_write_to_open_fd_not_detected_as_file(self):
        # echo text >&3 — writing to already-open fd, not a new file
        ps = paths(extract_redirects('echo text >&3', 1))
        self.assertEqual(ps, [])

    def test_variable_path_has_variable_flag(self):
        results = extract_redirects('echo hi > $OUT', 1)
        self.assertTrue(results[0].has_variable)

    def test_literal_path_no_variable_flag(self):
        results = extract_redirects('echo hi > out.txt', 1)
        self.assertFalse(results[0].has_variable)

    def test_mktemp_in_subshell_detected(self):
        ps = paths(extract_command_outputs('TMP=$(mktemp)', 1))
        self.assertIn('<mktemp>', ps)

    def test_pipeline_tee_detected(self):
        ps = paths(extract_command_outputs('generate_data | tee result.txt | wc -l', 1))
        self.assertIn('result.txt', ps)

    def test_no_crash_on_empty_line(self):
        self.assertEqual(extract_redirects('', 1), [])
        self.assertEqual(extract_command_outputs('', 1), [])
        self.assertEqual(extract_awk_outputs('', 1), [])

    def test_no_crash_on_comment_only(self):
        self.assertEqual(extract_redirects('# > out.txt', 1), [])

    def test_cp_single_arg_no_output(self):
        # cp with only source — no destination to report
        ps = paths(extract_command_outputs('cp src.sh', 1))
        self.assertEqual(ps, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
