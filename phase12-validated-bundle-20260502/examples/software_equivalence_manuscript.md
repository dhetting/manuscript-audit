# Dependence-aware equivalence audit for a reproducible software workflow

## Abstract

We evaluate whether a lower-touch remote workflow produces site-level estimates that are
statistically equivalent to a higher-fidelity reference workflow. The study uses matched
rooftop observations, a transparent command-line pipeline, and open-source software.
Equivalence is evaluated with TOST and supported with sensitivity checks [@schuirmann1987].

## Introduction

Reproducible workflow papers with operational decision claims need an audit stack that checks
the software pathway, the statistical claims, and the evidentiary alignment. This manuscript
argues that a local command-line workflow improves transparency while preserving practical
relevance [@lakens2017]. Figure 1 summarizes the pipeline.

## Methods

We analyze paired differences under dependence and compute equivalence decisions with TOST.
The implementation is packaged as a Python CLI with configuration files, serialized artifacts,
and deterministic preprocessing. The study also includes a simulation study to stress the
workflow under dependence [@westlake1976].

## Results

The workflow reproduced all fixture analyses and produced an auditable routing table.
Table 1 lists the routed modules and Figure 1 shows the pipeline. We observed narrower
findings in the minimal stack and broader findings in the standard stack.

## Discussion

The combination of equivalence claims, simulation evidence, and software dissemination means
that only a conditional audit stack is appropriate.

## References

- Schuirmann, D. J. (1987). A comparison of the two one-sided tests procedure.
- Lakens, D. (2017). Equivalence tests.
- Westlake, W. J. (1976). Symmetrical confidence intervals for bioequivalence trials.
