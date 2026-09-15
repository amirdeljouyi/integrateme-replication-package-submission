import unittest

from src.metrics.convention_metrics import (
    assertion_conformance,
    assertion_conformance_components,
    assertion_families,
    assertion_idioms,
    convention_profile,
    fixture_reuse,
    import_conformance,
    imports,
    naming_conformance,
    naming_conformance_components,
    _name_shape,
    strip_noise,
    test_method_names,
)


class ConventionMetricsTest(unittest.TestCase):
    def test_url_literal_does_not_hide_later_assertion(self) -> None:
        source = '''
            class Example {
                void check() {
                    String url = "https://example.test/path";
                    fail("later assertion");
                }
            }
        '''

        cleaned = strip_noise(source)

        self.assertIn("fail", cleaned)
        self.assertEqual(1, assertion_families(source)["junit"])

    def test_spring_mockmvc_is_an_assertion_family(self) -> None:
        manual = '''
            import org.springframework.test.web.servlet.MockMvc;
            class Manual {
                @Test void shouldRespond() throws Exception {
                    mvc.perform(get("https://example.test"))
                       .andExpect(status().isOk())
                       .andExpect(view().name("index"));
                }
            }
        '''
        candidate = '''
            class Candidate {
                @Test void shouldRespond() throws Exception {
                    mvc.perform(get("/" )).andExpect(status().isOk());
                }
            }
        '''

        # Both use MockMvc, but the candidate adopts only one of the manual
        # suite's two equally weighted expectation idioms.
        self.assertEqual(0.75, assertion_conformance(candidate, manual))

    def test_assertion_conformance_distinguishes_junit_idioms(self) -> None:
        manual = '''
            class Manual {
                @Test void check() { assertEquals(expected, actual); }
            }
        '''
        candidate = '''
            class Candidate {
                @Test void check() { assertTrue(expected == actual); }
            }
        '''

        # The API-family component agrees and the idiom component does not.
        self.assertEqual(0.5, assertion_conformance(candidate, manual))
        self.assertEqual(
            {"junit:assert_true:comparison": 1}, assertion_idioms(candidate)
        )

    def test_assertion_conformance_ignores_junit_version(self) -> None:
        manual = '''
            import static org.junit.jupiter.api.Assertions.assertEquals;
            class Manual {
                @Test void check() { assertEquals(expected, actual); }
            }
        '''
        candidate = '''
            import static org.junit.Assert.assertEquals;
            class Candidate {
                @Test public void check() { assertEquals(expected, actual); }
            }
        '''

        # Sanitization may migrate JUnit 4 syntax to JUnit 5 for compatibility.
        # The assertion metric evaluates the assertion family and shape, not the
        # framework generation, so equivalent JUnit assertions fully conform.
        self.assertEqual(
            {"family": 1.0, "idiom": 1.0},
            assertion_conformance_components(candidate, manual),
        )
        self.assertEqual(1.0, assertion_conformance(candidate, manual))

    def test_assertion_conformance_weights_the_assertion_mix(self) -> None:
        manual = '''
            class Manual {
                @Test void check() {
                    assertEquals(a, b);
                    assertEquals(c, d);
                    assertEquals(e, f);
                    assertTrue(valid);
                }
            }
        '''
        candidate = '''
            class Candidate {
                @Test void check() {
                    assertEquals(a, b);
                    assertTrue(valid);
                    assertTrue(ready);
                    assertTrue(done);
                }
            }
        '''

        # Family overlap is 1; the normalized idiom overlap is 0.5.
        self.assertEqual(0.75, assertion_conformance(candidate, manual))
        self.assertEqual(
            {"family": 1.0, "idiom": 0.5},
            assertion_conformance_components(candidate, manual),
        )

    def test_assertj_terminal_operation_is_part_of_the_idiom(self) -> None:
        manual = '''
            import static org.assertj.core.api.Assertions.assertThat;
            class Manual { @Test void check() { assertThat(value).isEqualTo(3); } }
        '''
        candidate = '''
            import static org.assertj.core.api.Assertions.assertThat;
            class Candidate { @Test void check() { assertThat(value).isNotNull(); } }
        '''

        self.assertEqual(0.5, assertion_conformance(candidate, manual))

    def test_explicit_truth_assertthat_is_not_counted_twice(self) -> None:
        source = '''
            class Example {
                @Test void check() { Truth.assertThat(value).isEqualTo(3); }
            }
        '''

        self.assertEqual(1, assertion_families(source)["truth"])
        self.assertEqual({"truth:isEqualTo": 1}, assertion_idioms(source))

    def test_method_fallback_recognizes_junit3_names_after_new_java_syntax(self) -> None:
        source = '''
            record UnsupportedByJavalang(String value) {}
            class Manual {
                @Test
                public void testSimple() throws Exception {
                }

                public void helperMethod() {
                }
            }
        '''

        self.assertEqual(["testSimple"], test_method_names(source))

    def test_imports_accept_wildcards_and_missing_semicolons(self) -> None:
        candidate = '''
            import static org.junit.Assert.*
            import static org.mockito.Mockito.*;
            class Candidate {}
        '''
        manual = '''
            import static org.junit.Assert.*;
            class Manual {}
        '''

        self.assertEqual(
            {"org.junit.Assert.*", "org.mockito.Mockito.*"}, imports(candidate)
        )
        self.assertEqual(0.5, import_conformance(candidate, manual))

    def test_fixture_nonuse_is_zero_when_reference_has_helpers(self) -> None:
        manual = '''
            class Manual {
                Widget createWidget() { return new Widget(); }
                @Test void shouldWork() { createWidget(); }
            }
        '''
        candidate = '''
            class Candidate {
                @Test void shouldWork() { assertTrue(true); }
            }
        '''

        self.assertEqual(0.0, fixture_reuse(candidate, manual))

    def test_name_parser_handles_acronyms_digits_and_underscores(self) -> None:
        self.assertEqual(
            ("should", "underscore", 3),
            _name_shape("shouldParseHTTP2_response"),
        )

    def test_naming_style_accepts_codominant_mixed_conventions(self) -> None:
        manual = '''
            class Manual {
                @Test void testClosesConnection() {}
                @Test void should_open_connection() {}
            }
        '''
        candidate = '''
            class Candidate {
                @Test void testReopensConnection() {}
                @Test void should_reject_invalid_state() {}
            }
        '''

        components = naming_conformance_components(candidate, manual)

        self.assertIsNotNone(components)
        self.assertEqual(1.0, components["style"])

    def test_naming_style_rejects_unseen_joint_combination(self) -> None:
        manual = '''
            class Manual {
                @Test void testClosesConnection() {}
                @Test void should_open_connection() {}
            }
        '''
        candidate = '''
            class Candidate { @Test void test_reopens_connection() {} }
        '''

        components = naming_conformance_components(candidate, manual)

        self.assertIsNotNone(components)
        self.assertEqual(0.0, components["style"])
        self.assertGreater(components["clarity"], 0.0)

    def test_numeric_placeholder_has_no_naming_clarity(self) -> None:
        manual = '''class Manual { @Test void testClose() {} }'''
        candidate = '''class Candidate { @Test void test17() {} }'''

        components = naming_conformance_components(candidate, manual)

        self.assertEqual({"style": 1.0, "clarity": 0.0}, components)
        self.assertEqual(0.5, naming_conformance(candidate, manual))

    def test_long_clear_name_is_not_penalized_for_exceeding_reference(self) -> None:
        manual = '''class Manual { @Test void shouldClose() {} }'''
        candidate = '''
            class Candidate {
                @Test void shouldCloseConnectionWhenIdle() {}
            }
        '''

        self.assertEqual(
            {"style": 1.0, "clarity": 1.0},
            naming_conformance_components(candidate, manual),
        )

    def test_repetition_and_mechanical_narration_reduce_clarity(self) -> None:
        manual = '''
            class Manual { @Test void testCreatesConfiguredWidget() {} }
        '''
        clear = '''
            class Candidate { @Test void testCreatesWidgetFromInputs() {} }
        '''
        repeated = '''
            class Candidate { @Test void testCreatesCreatesCreates() {} }
        '''
        mechanical = '''
            class Candidate {
                @Test void testCreatesWidgetTaking2ArgumentsAndCallsConfigure() {}
            }
        '''

        clear_score = naming_conformance_components(clear, manual)["clarity"]
        repeated_score = naming_conformance_components(repeated, manual)["clarity"]
        mechanical_score = naming_conformance_components(mechanical, manual)["clarity"]
        self.assertGreater(clear_score, repeated_score)
        self.assertGreater(clear_score, mechanical_score)

    def test_concise_name_remains_clear_for_concise_reference(self) -> None:
        manual = '''class Manual { @Test void testClose() {} }'''
        candidate = '''class Candidate { @Test void testOpen() {} }'''

        self.assertEqual(1.0, naming_conformance(candidate, manual))

    def test_empty_candidate_and_missing_reference_names(self) -> None:
        manual = '''class Manual { @Test void shouldWork() {} }'''
        empty = '''class Candidate { void helper() {} }'''
        no_tests = '''class Manual { void helper() {} }'''

        self.assertEqual(
            {"style": 0.0, "clarity": 0.0},
            naming_conformance_components(empty, manual),
        )
        self.assertIsNone(naming_conformance(empty, no_tests))

    def test_naming_scores_are_deterministic_and_bounded(self) -> None:
        manual = '''
            class Manual {
                @Test void shouldParseHTTPResponse() {}
                @Test void when_empty_returns_default() {}
            }
        '''
        candidate = '''
            class Candidate {
                @Test void shouldParseHTTP2Response() {}
                @Test void test0() {}
            }
        '''

        first = naming_conformance_components(candidate, manual)
        second = naming_conformance_components(candidate, manual)
        self.assertEqual(first, second)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in first.values()))

    def test_profile_exposes_naming_components_without_double_weighting(self) -> None:
        manual = '''
            import static org.junit.jupiter.api.Assertions.assertTrue;
            class Manual {
                @Test void shouldCloseConnection() { assertTrue(ready); }
            }
        '''
        candidate = '''
            import static org.junit.jupiter.api.Assertions.assertTrue;
            class Candidate {
                @Test void shouldCloseConnectionWhenIdle() { assertTrue(ready); }
            }
        '''

        profile = convention_profile(candidate, manual)
        output = profile.as_dict()
        self.assertEqual(1.0, output["naming_style_conformance"])
        self.assertEqual(1.0, output["naming_clarity"])
        self.assertEqual(1.0, output["naming_conformance"])
        self.assertEqual(1.0, output["conformance_score"])


if __name__ == "__main__":
    unittest.main()
