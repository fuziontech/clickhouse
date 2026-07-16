#include <Parsers/QueryParameterVisitor.h>
#include <Parsers/ASTQueryParameter.h>
#include <Parsers/ASTSetQuery.h>
#include <Parsers/ASTWithAlias.h>
#include <Parsers/Access/ASTCreateUserQuery.h>
#include <Parsers/Access/ASTGrantQuery.h>
#include <Parsers/Access/ASTRolesOrUsersSet.h>
#include <Parsers/FieldFromAST.h>
#include <Parsers/ParserQuery.h>
#include <Parsers/parseQuery.h>

#include <string_view>


namespace DB
{

class QueryParameterVisitor
{
public:
    explicit QueryParameterVisitor(NameToNameMap & parameters)
        : query_parameters(parameters)
    {
    }

    void visit(const ASTPtr & ast)
    {
        if (const auto & query_parameter = ast->as<ASTQueryParameter>())
            visitQueryParameter(*query_parameter);
        else if (const auto * set_query = ast->as<ASTSetQuery>())
            visitSetQuery(*set_query);
        else
        {
            /// A parametrized alias (`expr AS {name:Identifier}`) is stored as a member
            /// rather than as a child and must be discovered here explicitly.
            if (const auto * with_alias = dynamic_cast<const ASTWithAlias *>(ast.get()); with_alias && with_alias->parametrised_alias)
                visitQueryParameter(*with_alias->parametrised_alias);

            auto visit_ignored_roles_or_users = [&](const ASTRolesOrUsersSet * roles_or_users)
            {
                if (!roles_or_users || roles_or_users->hasQueryParameters())
                    return;

                for (const auto & name : roles_or_users->ignored_query_parameter_names)
                    query_parameters[name] = "Identifier";
            };

            if (const auto * grant_query = ast->as<ASTGrantQuery>())
            {
                visit_ignored_roles_or_users(grant_query->roles.get());
                visit_ignored_roles_or_users(grant_query->grantees.get());
            }
            else if (const auto * create_user_query = ast->as<ASTCreateUserQuery>())
            {
                visit_ignored_roles_or_users(create_user_query->roles.get());
                visit_ignored_roles_or_users(create_user_query->default_roles.get());
                visit_ignored_roles_or_users(create_user_query->grantees.get());
            }
            else if (const auto * roles_or_users = ast->as<ASTRolesOrUsersSet>())
                for (const auto & name : roles_or_users->ignored_query_parameter_names)
                    query_parameters[name] = "Identifier";

            for (const auto & child : ast->children)
                visit(child);
        }
    }

private:
    NameToNameMap & query_parameters;

    void visitQueryParameter(const ASTQueryParameter & query_parameter)
    {
        query_parameters[query_parameter.name] = query_parameter.type;
    }

    /// A setting value can be a query parameter, e.g. `SETTINGS max_threads = {threads:UInt64}`.
    /// The parser stores it as an ASTQueryParameter wrapped into a Field (see ParserSetQuery),
    /// so it is not reachable via ast->children and must be discovered here explicitly.
    void visitSetQuery(const ASTSetQuery & set_query)
    {
        for (const auto & change : set_query.changes)
        {
            CustomType custom;
            if (!change.value.tryGet<CustomType>(custom) || std::string_view(custom.getTypeName()) != FieldFromASTImpl::name)
                continue;

            const auto & value_ast = dynamic_cast<const FieldFromASTImpl &>(custom.getImpl()).ast;
            if (const auto * query_parameter = value_ast->as<ASTQueryParameter>())
                visitQueryParameter(*query_parameter);
        }
    }
};


NameSet analyzeReceiveQueryParams(const std::string & query)
{
    NameToNameMap query_params;
    const char * query_begin = query.data();
    const char * query_end = query.data() + query.size();

    ParserQuery parser(query_end);
    ASTPtr extract_query_ast = parseQuery(parser, query_begin, query_end, "analyzeReceiveQueryParams", 0, DBMS_DEFAULT_MAX_PARSER_DEPTH, DBMS_DEFAULT_MAX_PARSER_BACKTRACKS);
    QueryParameterVisitor(query_params).visit(extract_query_ast);

    NameSet query_param_names;
    for (const auto & query_param : query_params)
        query_param_names.insert(query_param.first);
    return query_param_names;
}

NameSet analyzeReceiveQueryParams(const ASTPtr & ast)
{
    NameToNameMap query_params;
    QueryParameterVisitor(query_params).visit(ast);
    NameSet query_param_names;
    for (const auto & query_param : query_params)
        query_param_names.insert(query_param.first);
    return query_param_names;
}

NameToNameMap analyzeReceiveQueryParamsWithType(const ASTPtr & ast)
{
    NameToNameMap query_params;
    QueryParameterVisitor(query_params).visit(ast);
    return query_params;
}


}
