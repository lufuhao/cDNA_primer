#!/usr/bin/python
"""Add arugment Parser for subcommand `classify` and `cluster`."""
import argparse
import random
from pbtools.pbtranscript.ClusterOptions import IceOptions

__all__ = ["add_classify_arguments",
           "add_cluster_arguments",
           "add_subset_arguments"]


__author__ = "etseng@pacificbiosciences.com, yli@pacificbiosciences.com"


def add_classify_arguments(parser):
    """Add arguments for subcommand `classify`."""
    helpstr = "Input fasta file (usually filtered_subreads.fasta or " + \
              "reads_of_insert.fasta)"
    parser.add_argument(type=str,
                        dest="readsFN",
                        help=helpstr)

    helpstr = "Output fasta file"
    parser.add_argument(type=str,
                        dest="outReadsFN",
                        help=helpstr)

    helpstr = "Output full-length non-chimeric reads in fasta"
    parser.add_argument("--flnc",
                        dest="flnc_fa",
                        type=str,
                        default=None,
                        help=helpstr)

    helpstr = "Output non-full-length reads in fasta"
    parser.add_argument("--nfl",
                        dest="nfl_fa",
                        type=str,
                        default=None,
                        help="Output non-full-length reads in fasta.")

    hmm_group = parser.add_argument_group("HMMER options")

    helpstr = "Directory to store HMMER output (default: output/)"
    hmm_group.add_argument("-d", "--outDir",
                           type=str,
                           dest="outDir",
                           default="output",
                           help=helpstr)

    hmm_group.add_argument("-p", "--primer",
                           type=str,
                           dest="primerFN",
                           default=None,
                           help="Primer fasta file (default: primers.fa)")

    hmm_group.add_argument("--cpus",
                           default=8,
                           type=int,
                           dest="cpus",
                           help="Number of CPUs to run HMMER (default: 8)")

    hmm_group.add_argument("--report",
                           default=None,
                           type=str,
                           dest="primerReportFN",
                           help="CSV file to output primer info (" + \
                                "default: *.primer_info.csv")

    hmm_group.add_argument("--summary",
                           default=None,
                           type=str,
                           dest="summary_fn",
                           help="TXT file to output classsify summary (" + \
                                "default: *.classify_summary.txt")

    chi_group = parser.add_argument_group("Chimera detection options")

    helpstr = "Minimum sequence length to output (default: 50)"
    chi_group.add_argument("--min_seq_len",
                           type=int,
                           dest="min_seq_len",
                           default=50,
                           help=helpstr)

    helpstr = "Minimum phmmer score for primer hit (default: 10)"
    chi_group.add_argument("--min_score",
                           dest="min_score",
                           type=float,
                           default=10,
                           help=helpstr)

    helpstr = "Search primers within windows of length searchPrimerWindow " + \
              "(default: 100)."
    chi_group.add_argument("--primer_search_window",
                           dest="primer_search_window",
                           type=int,
                           default=100,
                           help=argparse.SUPPRESS)

    helpstr = "Minimum distance the primer hit has to be from " + \
              "the end of sequence (default: 100)."
    chi_group.add_argument("--min_dist_from_end",
                           dest="min_dist_from_end",
                           type=int,
                           default=100,
                           help=argparse.SUPPRESS)

    helpstr = "Maximum distance between adjacent primer hits to " + \
              "consider as chimera (default: 50)."
    chi_group.add_argument("--max_adjacent_hit_dist",
                           dest="max_adjacent_hit_dist",
                           type=int,
                           default=50,
                           help=argparse.SUPPRESS)

    read_group = parser.add_argument_group("Read extraction options")
    helpstr = "FL does not require polyA tail (default: turned off)"
    read_group.add_argument("--ignore_polyA",
                           dest="ignore_polyA", 
                           default=False,
                           action="store_true",
                           help=helpstr)

#    helpstr = "Change read id to reflect trimming (default: off)."
#    parser.add_argument("--changeReadId",
#                        dest="changeReadId",
#                        action="store_const",
#                        const=True,
#                        default=False,
#                        help=argparse.SUPPRESS)
#
#    helpstr = "Trim polyA tails from reads (default: on)."
#    parser.add_argument("--trimPolyA",
#                        dest="trimPolyA",
#                        action="store_const",
#                        const=False,
#                        default=True,
#                        help=argparse.SUPPRESS)
#


def add_ice_options(parser):
    """Add Ice options as a group to parser, return parser"""
    ice_group = parser.add_argument_group("ICE options")
    ice_group.add_argument("--cDNA_size",
                           type=str,
                           dest="cDNA_size",
                           action="store",
                           choices=IceOptions.cDNA_sizeBins(),
                           default="under1k",
                           help="Estimated cDNA size.")

    ice_group.add_argument("--quiver",
                           dest="quiver",
                           default=False,
                           action="store_true",
                           help="Call quiver to polish consensus isoforms " + \
                                "using non-full-length non-chimeric reads " + \
                                "of insert. WARNING: not enabled yet.")
    return parser


def add_sge_options(parser, blasr_nproc=False, quiver_nproc=False):
    """Add Sge options as a group to parser, return parser."""
    sge_group = parser.add_argument_group("SGE environment options")

    sge_group.add_argument("--use_sge",
                           dest="use_sge",
                           default=False,
                           action="store_true",
                           help="The maximum number of jobs that will " + \
                                "be submitted to SGE concurrently.")

    sge_group.add_argument("--max_sge_jobs",
                           type=int,
                           dest="max_sge_jobs",
                           default=30,
                           action="store",
                           help="The maximum number of jobs that will " +
                                "be submitted to SGE concurrently.")

    sge_group.add_argument("--unique_id",
                           type=int,
                           dest="unique_id",
                           action="store",
                           default=random.randint(1,100000000),
                           help="Unique ID for submitting SGE jobs.")
    if blasr_nproc is True:
        sge_group.add_argument("--blasr_nproc",
                               type=int,
                               dest="blasr_nproc",
                               action="store",
                               default=24,
                               help="Number of cores for each BLASR job.")
    if quiver_nproc is True:
        sge_group.add_argument("--quiver_nproc",
                               dest="quiver_nproc",
                               type=int,
                               default=8,
                               help="Number of CPUs each quiver job uses.")
    return parser


def add_polished_isoforms_arguments(parser):
    """
    Add arguments of polished high-quality|low-quality isoforms in
    FASTA|FASTQ files.
    """
    parser.add_argument("--hq_isoforms_fa",
                        default=None,
                        type=str,
                        dest="hq_isoforms_fa",
                        help="Quiver polished, high quality isoforms " +
                        "in fasta, default: out_dir/output/all_quivered_hq.fa")

    parser.add_argument("--hq_isoforms_fq",
                        default=None,
                        type=str,
                        dest="hq_isoforms_fq",
                        help="Quiver polished, high quality isoforms " +
                        "in fastq, default: out_dir/output/all_quivered_hq.fq")

    parser.add_argument("--lq_isoforms_fa",
                        default=None,
                        type=str,
                        dest="lq_isoforms_fa",
                        help="Quiver polished, low quality isoforms " +
                        "in fasta, default: out_dir/output/all_quivered_lq.fa")

    parser.add_argument("--lq_isoforms_fq",
                        default=None,
                        type=str,
                        dest="lq_isoforms_fq",
                        help="Quiver polished, low quality isoforms " +
                        "in fastq, default: out_dir/output/all_quivered_lq.fq")
    return parser


def add_fofn_arguments(parser, ccs_fofn=False, bas_fofn=False,
                       fasta_fofn=False):
    """Add ccs_fofn, bas_fofn, fasta_fofn arguments."""
    helpstr = "A FOFN of ccs.h5 files (e.g., reads_of_insert.fofn), " + \
              "which contain quality values of reads of insert. " + \
              "If not given, assume there is no QV information available."
    if ccs_fofn is True:
        parser.add_argument("--ccs_fofn",
                            dest="ccs_fofn",
                            type=str,
                            default=None,
                            action="store",
                            help=helpstr)

    helpstr = "A FOFN of bax/bas.h5 files (e.g., input.fofn), " + \
              "which contain quality values of raw reads and subreads"
    if bas_fofn is True:
        parser.add_argument("--bas_fofn",
                            dest="bas_fofn",
                            type=str,
                            default=None,
                            action="store",
                            help=helpstr)

    if fasta_fofn is True:
        parser.add_argument("--fasta_fofn",
                            dest="fasta_fofn",
                            default=None,
                            help="A FOFN of trimmed subreads fasta, e.g." +
                                 "input.fasta.fofn")
    return parser


def add_flnc_fa_argument(parser, positional=False, required=False):
    """Add fasta arguments: flnc_fa, can be positional or non-positional,
    required or not required."""
    helpstr = "Input full-length non-chimeric reads in fasta format, " + \
              "used for clustering consensus isoforms, e.g., isoseq_flnc.fasta"
    if positional is True:
        parser.add_argument("flnc_fa", type=str, help=helpstr)
    else:
        assert(required is True or required is False)
        parser.add_argument("--flnc_fa", type=str, dest="flnc_fa",
                            required=required, help=helpstr)
    return parser


def add_nfl_fa_argument(parser, positional=False, required=False):
    """Add nfl_fa or --nfl_fa, can be positional  or non-positional,
    required or not required."""
    helpstr = "Input non-full-length reads in fasta format, used for " + \
              "polishing consensus isoforms, e.g., isoseq_nfl.fasta"
    if positional is True:
        parser.add_argument("nfl_fa", type=str, help=helpstr)
    else:
        assert(required is True or required is False)
        parser.add_argument("--nfl_fa", type=str, dest="nfl_fa",
                            required=required, help=helpstr)
    return parser


def add_cluster_arguments(parser):
    """Add arguments for subcommand `cluster`."""
    parser = add_flnc_fa_argument(parser, positional=True)

    helpstr = "Output predicted (unpolished) consensus isoforms in fasta file."
    parser.add_argument(type=str,
                        dest="consensusFa",
                        help=helpstr)

    parser = add_nfl_fa_argument(parser, positional=False, required=False)
    parser = add_fofn_arguments(parser, ccs_fofn=True, bas_fofn=True, fasta_fofn=True)

    helpstr = "Directory to store temporary and output cluster files." + \
        "(default: output/)"
    parser.add_argument("-d", "--outDir",
                        type=str,
                        dest="root_dir",
                        default="output",
                        help=helpstr)

    parser.add_argument("--report",
                        default=None,
                        type=str,
                        dest="report_fn",
                        help="CSV file to output cluster info (" + \
                             "default: *.primer_info.csv")

    parser.add_argument("--summary",
                        default=None,
                        type=str,
                        dest="summary_fn",
                        help="TXT file to output cluster summary (" + \
                             "default: *.cluster_summary.txt")


    parser = add_polished_isoforms_arguments(parser)

    # Add Ice options, including --quiver
    parser = add_ice_options(parser)

    # Add Sge options
    parser = add_sge_options(parser, blasr_nproc=True, quiver_nproc=True)


def add_subset_arguments(parser):
    """Add arguments for subcommand `subset`."""
    helpstr = "Input fasta file (usually isoseq_draft.fasta)"
    parser.add_argument(type=str,
                        dest="readsFN",
                        help=helpstr)

    helpstr = "Output fasta/txt file"
    parser.add_argument(type=str,
                        dest="outFN",
                        help=helpstr)

    group = parser.add_mutually_exclusive_group()
    helpstr = "Reads to outut must be Full-Length, with 3' " + \
              "primer and 5' primer and polyA tail seen."
    group.add_argument('--FL',
                       dest='FL',
                       const=1,  # 0: non-FL, 1: FL, 2: either
                       action='store_const',
                       default=2,
                       help=helpstr)

    helpstr = "Reads to output must be Non-Full-Length reads."
    group.add_argument('--nonFL',
                       dest='FL',  # 0: non-FL, 1: FL, 2: either
                       const=0,
                       action='store_const',
                       help=helpstr)

    group = parser.add_mutually_exclusive_group()
    helpstr = "Reads to output must be non-chimeric reads."
    group.add_argument('--nonChimeric',
                       dest='nonChimeric',
                       const=1,
                       default=2,
                       action='store_const',
                       help=helpstr)

    group.add_argument('--chimeric',
                       dest='nonChimeric',
                       const=0,
                       action='store_const',
                       help=helpstr)

    helpstr = "Only print read lengths, no read names and sequences."
    parser.add_argument('--printReadLengthOnly',
                        default=False,
                        dest='printReadLengthOnly',
                        action='store_true',
                        help=helpstr)

    helpstr = "FL does not require polyA tail (default: turned off)"
    parser.add_argument("--ignore_polyA",
                        dest="ignore_polyA", 
                        default=False,
                        action="store_true",
                        help=helpstr)

#    helpstr = "Change read id to reflect trimming (default: off)."
#    parser.add_argument("--changeReadId",


# import sys
# if __name__ == "__main__":
#     parser = argparse.ArgumentParser()
#     add_cluster_arguments(parser)
#     opt = parser.parse_args(sys.argv[1:])
#     print opt
