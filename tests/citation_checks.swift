import Foundation

// Host-verified citations: factual lines must point at a span the host read.
@main
struct CitationChecks {
    static var passed = 0
    static func check(_ ok: Bool, _ name: String) {
        if ok { passed += 1 } else { FileHandle.standardError.write("FAILED: \(name)\n".data(using: .utf8)!); exit(1) }
    }
    static func main() {
        let s1 = Citations.Span(tag: "S1", id: "doc_a", start: 0, end: 1500, text: "We estimate a difference-in-differences model with village fixed effects. Household income rose by 11.6 percent (95% CI 4.2 to 19.0).", note: "DiD with village fixed effects; income +11.6% (CI 4.2–19.0).")
        let s2 = Citations.Span(tag: "S2", id: "doc_a", start: 76000, end: 77500, text: "Attrition reached 14 percent overall and 19 percent in the poorest quintile.", note: "Attrition 14% overall, 19% poorest quintile.")
        let spans = [s1, s2]

        // 1. A correctly cited line is kept; a wrongly cited line is re-attributed; an unsupported line is removed.
        let answer = """
        **Identification**
        The authors use a difference-in-differences design with village fixed effects [S1].
        Attrition was 14% overall and 19% among the poorest quintile [S1].
        One section mentioned there was no income effect at all, which contradicts the headline estimate.
        Household income rose by 11.6% (95% CI 4.2–19.0).
        """
        let result = Citations.verify(answer: answer, spans: spans)
        check(result.text.contains("village fixed effects [S1]"), "cited line kept")
        check(result.text.contains("poorest quintile. [S2]"), "wrong tag re-attributed to the supporting span")
        check(!result.text.contains("no income effect"), "unsupported claim removed")
        check(result.dropped == 1, "dropped count is 1 (got \(result.dropped))")
        check(result.text.contains("11.6% (95% CI 4.2–19.0). [S1]"), "uncited factual line attributed")
        check(result.attributed == 2, "attributed count is 2 (got \(result.attributed))")
        check(result.text.contains("Sources: S1 = doc_a characters 0–1500; S2 = doc_a characters 76000–77500"), "source list appended")
        check(result.text.contains("**Identification**"), "headers and short lines untouched")

        // 2. Figures absent from the cited span fail the citation even when words match.
        let fake = "Attrition reached 23% in the poorest quintile [S2]."
        let r2 = Citations.verify(answer: fake, spans: spans)
        check(r2.dropped == 1 && !r2.text.contains("23%"), "figure not in span removes the line")

        // 3. Years and list numbers do not count as figures; a short connective line is not factual.
        let r3 = Citations.verify(answer: "1. The design is a difference-in-differences model with village fixed effects, published in 2026 [S1].\nSee below.", spans: spans)
        check(r3.dropped == 0 && r3.text.contains("[S1]"), "years and list markers ignored")

        // 4. No spans: the answer passes through untouched.
        let r4 = Citations.verify(answer: "Anything goes 99%.", spans: [])
        check(r4.text == "Anything goes 99%." && r4.dropped == 0, "no spans means no filtering")

        // 6. A sentence combining facts from two spans is kept when the cited spans together support it, and
        //    attributed to the holders when uncited (the c1 live case: 11.6% from S1 plus attrition from S2).
        let mixed = "Household income rose by 11.6% (95% CI 4.2 to 19.0) while attrition reached 14 percent overall [S1, S2]."
        let r6 = Citations.verify(answer: mixed, spans: spans)
        check(r6.dropped == 0 && r6.text.contains("[S1, S2]"), "multi-span citation verified as a set")
        let mixedUncited = "Household income rose by 11.6% while attrition reached 14 percent overall."
        let r7 = Citations.verify(answer: mixedUncited, spans: spans)
        check(r7.dropped == 0 && r7.attributed == 1 && r7.text.contains("[S1, S2]"), "uncited multi-span sentence attributed to both holders (got: \(r7.text))")
        let impossible = "Household income rose by 11.6% while attrition reached 41 percent overall."
        check(Citations.verify(answer: impossible, spans: spans).dropped == 1, "a figure absent from every span still removes the line")

        // 7. Lead-ins and connective sentences without figures are not claims (the d1 live case).
        let leadIns = "Here are the key findings from the *Rural Roads Impact Evaluation* report, based on the spans read:\nThe final recommendations focus on sustainability and monitoring:\nI hope this helps you with your review of the programme today."
        let r8 = Citations.verify(answer: leadIns, spans: spans)
        check(r8.dropped == 0 && r8.text.contains("sustainability and monitoring:"), "lead-in lines kept (got dropped \(r8.dropped))")
        let wordy = Citations.verify(answer: "The identification strategy uses a difference-in-differences model with village fixed effects throughout.", spans: spans)
        check(wordy.dropped == 0 && wordy.attributed == 1 && wordy.text.contains("[S1]"), "a wordy claim supported by S1 is attributed, not dropped")
        check(Citations.verify(answer: "The authors conclude that fertiliser subsidies explain most of the measured consumption growth in treated districts.", spans: spans).dropped == 1, "an unsupported wordy claim is still removed")

        // 8. Thinking aloud is removed from answers; findings stay.
        let aloud = "Wait, all three are affordable? Let me re-check the cost per unit.\nAll three options are affordable at 900 USD per pupil-year [S1].\nHmm, this contradicts my calculation.\n**Dominated option:** none [S1].\nUnless \"affordable\" means something else."
        let calm = Citations.stripDeliberation(aloud)
        check(calm.removed == 3 && calm.text.contains("All three options are affordable") && calm.text.contains("**Dominated option:**"), "deliberation lines removed, findings kept (removed \(calm.removed))")
        check(Citations.stripDeliberation("The wait time for market access fell by 27 minutes [S4].").removed == 0, "a sentence that merely contains 'wait' is kept")

        // 9. Rounded figures: the tool said 851.85 and 1380000; the answer says 851.9 and 1,380,000.
        let tool = Citations.Span(tag: "T1", id: "cost_effectiveness_analysis", start: 0, end: 0, text: "{\"options\":[{\"name\":\"Conditional cash transfer\",\"total_cost\":1380000,\"effect\":1620,\"cost_per_unit\":851.85}]}", note: "")
        let rounded = Citations.verify(answer: "| 2 | Conditional cash transfer | 1,380,000 | 1,620 | **851.9** | [S1]", spans: spans + [tool])
        check(rounded.dropped == 0 && rounded.text.contains("[T1]"), "a figure that rounds to the tool's value is attributed to the tool result (got: \(rounded.text))")
        check(Citations.verify(answer: "Conditional cash transfer costs 861.9 per pupil-year.", spans: spans + [tool]).dropped == 1, "a figure that does not round to any value is still removed")
        check(rounded.text.contains("T1 = cost_effectiveness_analysis result"), "tool spans are listed as results in Sources")

        // 10. Trailing deliberation inside a line is dropped sentence by sentence; the finding before it stays.
        let trailing = Citations.stripDeliberation("The tool's dominance field says all three are non-dominated [T1]. Let me double-check this manually:")
        check(trailing.removed == 1 && trailing.text.contains("non-dominated [T1]") && !trailing.text.contains("double-check"), "trailing deliberation sentence removed (got: \(trailing.text))")

        // 5. Scope and Sources lines are never treated as claims.
        let r5 = Citations.verify(answer: "Scope: 6 spans matching the question were read, not the whole document.\n\nIncome rose by 11.6% [S1].", spans: spans)
        check(r5.dropped == 0 && r5.text.hasPrefix("Scope: 6 spans"), "scope line preserved")

        print("Citation checks passed: \(passed) assertions")
    }
}
