from dataclasses import dataclass
from typing import List, Dict, Tuple
from alectryon.literate import coq_partition, Comment, StringView
import re
import os

from utils import Path, SubSection, Parser, Case, ParserState, Position


@dataclass(frozen=True)
class CoqLine:
    file_name: str
    line_number: int
    is_end_comment: bool = False


@dataclass(frozen=True)
class CoqPosition(Position):
    file_positions: Dict[str, Tuple[int, int]]

    def html_str(self) -> str:
        return ", ".join([f"<a href='file:///{file_name}'><b>{self.get_file_name_without_path(file_name)}</b>: {start} - {end}</a>" for file_name, (start, end)
                          in self.file_positions.items()])
    def get_file_name_without_path(self,path) -> str:
        return path.split("/")[-1]

class COQParser(Parser):
    def __init__(self, files: List[Path], to_exclude: List[Path], title_regex: str = r"(22\.2(?:\.[0-9]{0,2}){1,3})",
                 spec_regex: str = r"^\(\*(\* )?>?>(.|\n)*?<<\*\)$",
                 case_regex: str = r"([a-zA-Z0-9\[\]]+) ::((?:.|\n)*)",
                 algo_regex: str = r"([0-9a-z]|i{2,})\. .*",
                 any_title_regex: str = r"^[ -]*?((?:[0-9]+\.)+[0-9]+)(?: .*?|)$"):
        self.files = files
        self.to_exclude = to_exclude
        self.title_regex = re.compile(title_regex)
        self.any_title_regex = re.compile(any_title_regex, re.MULTILINE)
        self.spec_regex = re.compile(spec_regex)
        self.case_regex = re.compile(case_regex)
        self.algo_regex = re.compile(algo_regex)

        self.coq_code, self.all_filenames = self.get_coq_code()
        self.comments = self.get_comment_lines()
        self.sections_by_number = self.get_comment_titles()

    @staticmethod
    def get_lines_num_from_paragraph(string_view: StringView) -> tuple[int, int]:
        original_string: str = string_view.s
        line_start = original_string.count("\n", 0, string_view.beg) + 1
        line_end = line_start + original_string.count("\n", string_view.beg, string_view.end)
        if original_string[string_view.end] == "\n":
            line_end -= 1
        return line_start, line_end

    @staticmethod
    def get_line_num(string_view: StringView) -> int:
        return string_view.s.count("\n", 0, string_view.beg)

    def _add_file(self, filename: str, files_dic: dict, all_filenames: list):
        if any([filename.startswith(excluded.uri) for excluded in self.to_exclude]):
            return
        with open(filename, "r") as f:
            coq_file = f.read()
            files_dic[filename] = coq_file
        all_filenames.append(filename)

    def get_coq_code(self) -> Tuple[Dict[str, str], List[str]]:
        files_dic = {}
        all_filenames = []
        for file in self.files:
            if file.is_dir:
                for root, dirs, files in os.walk(file.uri, topdown=False):
                    for name in files:
                        self._add_file(os.path.abspath(os.path.join(root, name)), files_dic, all_filenames)
            else:
                self._add_file(file.uri, files_dic, all_filenames)
        return files_dic, all_filenames

    def get_comment_lines(self) -> List[tuple[str, CoqLine]]:
        comments = []
        for filename in self.all_filenames:
            file = self.coq_code[filename]
            partition = coq_partition(file)
            for field in partition:
                if isinstance(field, Comment) and self.spec_regex.match(str(field.v)):
                    for line in str(field.v).splitlines():
                        line = self._parse_comment(line)
                        if line != "":
                            line_num = self.get_line_num(field.v)
                            comments.append((line, CoqLine(filename, line_num)))
                # avoid -1 at start, would have made no sense
                if len(comments) > 0:
                    comments.append(("", CoqLine(file, -1, True)))
        return comments

    # Completely arbitrary in our case
    def merge_comments(self, section1: SubSection, section2: SubSection):
        print("[WARNING] Merge called for ", section1, section2)
        title = section1.title if len(section1.title) > len(section2.title) else section2.title
        description_first = section1.description if len(section1.description) > len(
            section2.description) else section2.description
        description_second = section1.description if len(section1.description) <= len(
            section2.description) else section2.description
        pos: tuple[CoqPosition, CoqPosition] = section1.position, section2.position
        new_files = {}
        old_files = (pos[0].file_positions, pos[1].file_positions)
        for filename in old_files[0].keys() | old_files[1].keys():
            match (filename in old_files[0].keys(), filename in old_files[1].keys()):
                case (True, True):
                    new_files[filename] = (min(old_files[0][filename], old_files[1][filename]),
                                           max(old_files[0][filename], old_files[1][filename]))
                case (False, True):
                    new_files[filename] = old_files[1][filename]
                case (True, False):
                    new_files[filename] = old_files[0][filename]
                case _:
                    raise Exception("This should never happen")

        return (SubSection(title,
                           description_first + "\n" + description_second,
                           section1.cases.union(section2.cases),
                           CoqPosition(new_files)))

    """
    Gets the indices of the comments that contain the titles of the sections (comments that match the title_regex)
    """

    def get_comment_titles(self) -> Dict[str, SubSection]:
        title_indices = {}
        current_block = ""
        last_block_end = 0
        section_to_be_thrown_away = False
        for comment_index, comment in enumerate(self.comments):
            if type(comment) is int:
                continue
            if res2 := self.any_title_regex.match(comment[0]):
                print(res2.group(1), comment)
                if current_block != "" and not section_to_be_thrown_away:
                    if title_indices.get(current_block) is not None:
                        # This means the section was split
                        title_indices[current_block] = self.merge_comments(
                            self.parse_subsection((last_block_end, comment_index)), title_indices.get(current_block))
                    else:
                        title_indices[current_block] = self.parse_subsection((last_block_end, comment_index))
                    last_block_end = comment_index
                elif current_block != "" and section_to_be_thrown_away:
                    last_block_end = comment_index
                current_block = res2.group(1)
                section_to_be_thrown_away = self.title_regex.search(str(comment)) is None
        if not section_to_be_thrown_away:
            title_indices[current_block] = self.parse_subsection((last_block_end, len(self.comments)))
        return title_indices

    def _parse_title(self, title: str) -> str:

        lines = title.splitlines(keepends=False)
        title = lines[1].lstrip()
        for line in lines[3:-1]:
            title += "\n" + line.lstrip()
        return title + "\n"

    def _parse_comment(self, comment: str) -> str:
        return (comment.replace("\n", "").replace("(*>>", "").replace("<<*)", "")
                .replace("(** >>", "").lstrip().rstrip())

    # Pour les commentaires de 22.2.2.1, on peut soit:
    # - mettre un "-" ou "*" au début pour montrer qu'il s'agit d'un point
    # - mettre un commentaire avant qui indique qu'il s'agit d'un enchaînement de points
    def parse_subsection(self, comment_indices):
        comment_lines = self.comments[comment_indices[0]:comment_indices[1]]

        title = ""
        description = ""
        parser_state = ParserState.READING_TITLE
        cases = set()
        current_case = ""
        current_case_title = ["", ""]
        filenames = {}
        for parsed_comment, coq_line in comment_lines:
            # We are at the end of a comment
            if coq_line.is_end_comment:
                match parser_state:
                    case ParserState.READING_TITLE:
                        parser_state = ParserState.READING_DESCRIPTION
                    case ParserState.READING_DESCRIPTION:
                        pass
                    case ParserState.READING_CASES:
                        pass
                continue
            # Get file name
            filename = coq_line.file_name
            # If not already in, add it and add its current line
            if filenames.get(filename) is None:
                filenames[filename] = (coq_line.line_number, coq_line.line_number)
            # Otherwise update last line
            else:
                filenames[filename] = (filenames[filename][0], coq_line.line_number)
            if self.case_regex.match(parsed_comment):
                parser_state = ParserState.READING_CASES
                if current_case_title != ["", ""] or current_case != "":
                    cases.add(Case(current_case_title[0], current_case_title[1], current_case))
                match = self.case_regex.match(parsed_comment)
                current_case_title = [match.group(1), match.group(2)]
                current_case = ""

            else:
                match parser_state:
                    case ParserState.READING_TITLE:
                        if self.algo_regex.match(parsed_comment):
                            # If there is a start of an algorithm, but we are still building title, it means that there
                            # is only one case in the subsection and therefore we set its title to ""
                            parser_state = ParserState.READING_CASES
                            current_case = parsed_comment + "\n"
                        else:
                            title += parsed_comment
                            if "11.1.4" in title:
                                a = 123
                            parser_state = ParserState.READING_DESCRIPTION
                    case ParserState.READING_DESCRIPTION:
                        if self.algo_regex.match(parsed_comment):
                            # If there is a start of an algorithm, but we are still building description, it means that
                            # there is only one case in the subsection and therefore we set its title to ""
                            parser_state = ParserState.READING_CASES
                            current_case = parsed_comment + "\n"
                        else:
                            description += parsed_comment + " "
                    case ParserState.READING_CASES:
                        current_case += parsed_comment + "\n"

        if current_case != "":
            cases.add(Case(current_case_title[0], current_case_title[1], current_case))
        return SubSection(title, description, cases, CoqPosition(filenames))

    def get_section_for_comparison(self, section) -> SubSection:
        assert section in self.sections_by_number.keys()
        return self.sections_by_number[section]
