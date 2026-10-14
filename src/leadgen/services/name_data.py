"""Name and credential word lists used by the people finder (T08, `services/confirm.py`).

Data only, no logic. All entries are lower-case ASCII.

- COMMON_SURNAMES: romanised Chinese, Korean and Vietnamese surnames, including the common
  spelling variants (tan/chen/chan, lee/li, ng/wu, nguyen). Used to read East Asian name order.
- WESTERN_GIVEN_NAMES: common English given names. Used to tell "Chen Rachel" (surname, given
  name) from "Mei Xin Tan" (given name, surname).
- CREDENTIALS: post-nominals and certifications seen in profile names and headlines.

Limits of the heuristic: the lists are not exhaustive and come from general knowledge, not from
a statistical source. Some words are in more than one list ("ma" is a surname and a degree,
"son" and "moon" are English words, "ha" and "nam" are also Vietnamese given names). Callers
should strip credentials only after a comma or inside brackets, and should treat a name as
East Asian only when the surname test and the given-name test agree.
"""

COMMON_SURNAMES: frozenset[str] = frozenset(
    """
    wang li zhang liu chen yang huang zhao wu zhou xu sun ma zhu hu guo he lin gao luo
    zheng liang xie song tang han feng deng cao peng zeng xiao tian dong pan yuan cai
    jiang yu du ye cheng wei su lu ding ren shen yao tan chan tay ong ng goh koh lim teo
    chua chong lau leung cheung wong ho lam chow chu kwok yeo seah ang chia sim low khoo
    foo loh toh chew yap lee lai lok kong so tsang yip mak fung kwan tse lui kuo hsu hsieh
    lo chang tseng
    kim park choi jung kang cho yoon jang oh seo shin kwon hwang ahn yoo hong jeon moon
    bae baek heo nam roh noh ryu won chae woo im sohn son ko yi paek ha
    nguyen tran le pham hoang phan vu vo dang bui do ngo duong ly doan dinh dao truong mai
    trinh huynh phung vuong quach luu trieu thai tong thach bach ta phi ung
    """.split()
)

WESTERN_GIVEN_NAMES: frozenset[str] = frozenset(
    """
    aaron adam adrian alan albert alex alexander andrew andy anthony arthur austin ben
    benjamin bill billy bob brandon brian bruce bryan caleb cameron carl charles charlie
    chris christian christopher colin connor craig daniel danny dave david dean dennis derek
    dominic don donald douglas dylan eddie edward edwin eric ethan evan frank fred gabriel
    gary gavin george gerald gordon graham grant greg gregory guy harry
    henry howard ian isaac jack jacob james jason jay jeff jeffrey jeremy jerry jesse jim
    jimmy joe joel john johnny jonathan jordan joseph josh joshua justin keith ken kenneth
    kevin kyle larry lawrence leo leonard liam lewis logan louis lucas luke mark martin matt
    matthew max michael mike mitchell nathan nathaniel neil nicholas nick noah oliver oscar
    owen patrick paul pete peter philip phil ray raymond richard rick robert rob roger ronald
    ross roy russell ryan sam samuel scott sean seth shane simon stephen steve stuart ted
    thomas tim timothy tom tony tyler victor vincent william zachary
    abigail alice alison amanda amber amy andrea angela anna anne ashley audrey
    barbara beth bethany betty beverly brenda brittany carol caroline carrie catherine
    charlotte cheryl chloe christina christine claire clare colleen cynthia dana daniela
    danielle deborah debbie denise diana diane donna dorothy elaine eleanor elizabeth ellen
    emily emma erica erin eva evelyn faith felicity fiona gail gemma georgia gillian grace
    hannah heather helen holly hope irene isabel isabella jacqueline jane janet jasmine jean
    jennifer jenny jessica jill joan joanne josephine joy judith judy julia julie karen kate
    katherine kathleen kathryn kathy katie kelly kimberly laura lauren leah lily linda lisa
    lois lorraine louise lucy lydia lynn mandy margaret maria marie marina martha mary megan
    melanie melissa michelle mia miranda molly monica nadia natalie nicola nicole nina nora
    olivia paige pamela patricia paula pauline penny rachel rebecca rebekah regina rhonda
    rita rosa rose ruth sally samantha sandra sarah sharon shirley sophie stacey stephanie
    sue susan suzanne sylvia tamara tanya teresa tess theresa tina tracy valerie vanessa
    veronica victoria virginia wendy yvonne zoe
    """.split()
)

CREDENTIALS: frozenset[str] = frozenset(
    """
    mba dba edd phd msc ma ms ba bsc bs bcom beng meng llb llm md mbbs mph pgdip pgcert
    esq cpa fca acca cima acma cfa cma cia cisa cism cissp cdmp cfp chra cphr gphr sphr phr
    ihrp-cp ihrp-sp ihrp-sa shrm-cp shrm-scp cipd mcipd fcipd pmp prince2 itil csp wsho fsm
    nebosh aicd cgeit ccna ccnp cscp cpsm csm cspo psm mrics frics ceng
    """.split()
)
