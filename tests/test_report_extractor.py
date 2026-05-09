import unittest

try:
    from medical_image_locator_report_identify.report_extractor import (
        ReportTerm,
        _extract_exported_env_var,
        _parse_json_text,
        _parse_terms_payload,
        build_localization_prompt,
    )
except ModuleNotFoundError:
    from report_extractor import (
        ReportTerm,
        _extract_exported_env_var,
        _parse_json_text,
        _parse_terms_payload,
        build_localization_prompt,
    )


class ReportExtractorTests(unittest.TestCase):
    def test_extract_exported_env_var_reads_bashrc_style_export(self):
        text = 'export OPENAI_API_KEY="sk-test-value"\nexport OTHER=value\n'
        self.assertEqual(_extract_exported_env_var(text, "OPENAI_API_KEY"), "sk-test-value")

    def test_parse_json_text_handles_wrapped_json(self):
        payload = _parse_json_text('Result:\n{"terms": ["cistern", "tumor"]}\nDone')
        self.assertEqual(payload, {"terms": ["cistern", "tumor"]})

    def test_parse_terms_payload_deduplicates_and_coerces(self):
        payload = {
            "terms": [
                {"name": "Cistern", "query": "basal cistern", "aliases": ["cisterns"], "context": "visible", "source_sentence": ""},
                {"name": "cistern", "query": "duplicate", "aliases": [], "context": "", "source_sentence": ""},
                "tumor",
            ]
        }
        terms = _parse_terms_payload(payload)
        self.assertEqual([term.name for term in terms], ["Cistern", "tumor"])
        self.assertEqual(terms[0].prompt_query, "basal cistern")

    def test_build_localization_prompt_includes_no_guessing_rule(self):
        prompt = build_localization_prompt(ReportTerm(name="ventricular enlargement", aliases=["ventricle"]))
        self.assertIn("ventricular enlargement", prompt)
        self.assertIn("no_region_found", prompt)
        self.assertIn("Do not infer", prompt)


if __name__ == "__main__":
    unittest.main()
