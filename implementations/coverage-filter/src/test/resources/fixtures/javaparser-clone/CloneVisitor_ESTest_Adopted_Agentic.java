/*
 * Copyright (C) 2007-2010 Júlio Vilmar Gesser.
 * Copyright (C) 2011, 2013-2024 The JavaParser Team.
 *
 * This file is part of JavaParser.
 *
 * JavaParser can be used either under the terms of
 * a) the GNU Lesser General Public License as published by
 *     the Free Software Foundation, either version 3 of the License, or
 *     (at your option) any later version.
 * b) the terms of the Apache License
 *
 * You should have received a copy of both licenses in LICENCE.LGPL and
 * LICENCE.APACHE. Please refer to those files for details.
 *
 * JavaParser is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Lesser General Public License for more details.
 */

package com.github.javaparser.ast.visitor;

import static com.github.javaparser.StaticJavaParser.parseType;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNotSame;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.github.javaparser.JavaToken;
import com.github.javaparser.TokenRange;
import com.github.javaparser.ast.ArrayCreationLevel;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.Modifier;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.*;
import com.github.javaparser.ast.comments.BlockComment;
import com.github.javaparser.ast.comments.LineComment;
import com.github.javaparser.ast.expr.*;
import com.github.javaparser.ast.modules.ModuleRequiresDirective;
import com.github.javaparser.ast.modules.ModuleUsesDirective;
import com.github.javaparser.ast.stmt.*;
import com.github.javaparser.ast.type.*;
import java.util.Iterator;
import java.util.LinkedList;
import java.util.Optional;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class CloneVisitor_ESTest_Adopted_Agentic {
    CompilationUnit cu;

    @BeforeEach
    void setUp() {
        cu = new CompilationUnit();
    }

    @AfterEach
    void teardown() {
        cu = null;
    }

    @Test
    void cloneJavaDocTest() {
        NodeList<BodyDeclaration<?>> bodyDeclarationList = new NodeList<>();
        bodyDeclarationList.add(new AnnotationMemberDeclaration().setJavadocComment("javadoc"));
        bodyDeclarationList.add(new ConstructorDeclaration().setJavadocComment("javadoc"));
        bodyDeclarationList.add(new EnumConstantDeclaration().setJavadocComment("javadoc"));
        bodyDeclarationList.add(new FieldDeclaration().setJavadocComment("javadoc"));
        bodyDeclarationList.add(new InitializerDeclaration().setJavadocComment("javadoc"));
        bodyDeclarationList.add(new MethodDeclaration().setJavadocComment("javadoc"));

        NodeList<TypeDeclaration<?>> typeDeclarationList = new NodeList<>();
        AnnotationDeclaration annotationDeclaration = new AnnotationDeclaration();
        annotationDeclaration.setName("nnotationDeclarationTest");
        typeDeclarationList.add(annotationDeclaration.setJavadocComment("javadoc"));

        ClassOrInterfaceDeclaration classOrInterfaceDeclaration2 = new ClassOrInterfaceDeclaration();
        classOrInterfaceDeclaration2.setName("emptyTypeDeclarationTest");
        typeDeclarationList.add(classOrInterfaceDeclaration2.setJavadocComment("javadoc"));

        EnumDeclaration enumDeclaration = new EnumDeclaration();
        enumDeclaration.setName("enumDeclarationTest");
        typeDeclarationList.add(enumDeclaration.setJavadocComment("javadoc"));

        ClassOrInterfaceDeclaration classOrInterfaceDeclaration = new ClassOrInterfaceDeclaration();
        classOrInterfaceDeclaration.setName("classOrInterfaceDeclarationTest");
        typeDeclarationList.add(classOrInterfaceDeclaration.setJavadocComment("javadoc"));

        ClassOrInterfaceDeclaration classOrInterfaceDeclaration1 = new ClassOrInterfaceDeclaration();
        classOrInterfaceDeclaration1.setName("emptyTypeDeclarationTest1");
        typeDeclarationList.add(classOrInterfaceDeclaration2.setMembers(bodyDeclarationList));

        cu.setTypes(typeDeclarationList);
        CompilationUnit cuClone = (CompilationUnit) new CloneVisitor().visit(cu, null);

        NodeList<TypeDeclaration<?>> typeDeclarationListClone = cuClone.getTypes();
        Iterator<TypeDeclaration<?>> typeItr = typeDeclarationListClone.iterator();
        TypeDeclaration<?> typeDeclaration;
        while (typeItr.hasNext()) {
            typeDeclaration = typeItr.next();
            if (typeDeclaration.getMembers() == null) {
                assertEquals("javadoc", typeDeclaration.getComment().get().getContent());
            } else {
                Iterator<BodyDeclaration<?>> bodyItr =
                        typeDeclaration.getMembers().iterator();
                while (bodyItr.hasNext()) {
                    BodyDeclaration<?> bodyDeclaration = bodyItr.next();
                    assertEquals("javadoc", bodyDeclaration.getComment().get().getContent());
                }
            }
        }
    }

    @Test
    void cloneAnnotationOnWildcardTypeArgument() {
        Type type = parseType("List<@C ? extends Object>").clone();
        assertEquals("List<@C ? extends Object>", type.toString());
    }

    @Test
    void visitWildcardType_withBlockComment_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        TypeParameter bound = new TypeParameter("]0Cjw}+`@oK`WO2B");
        NodeList<AnnotationExpr> annotations = new NodeList<>();
        WildcardType wildcard = new WildcardType(bound, bound, annotations);
        BlockComment contextComment = new BlockComment();
        Visitable cloned = visitor.visit(wildcard, contextComment);
        assertTrue(cloned.equals(wildcard));
        assertNotSame(cloned, wildcard);
    }

    @Test
    void visitVarType_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        VarType varType = new VarType();
        Object context = new Object();
        Visitable cloned = visitor.visit(varType, context);
        assertNotSame(cloned, varType);
    }

    @Test
    void visitUnionType_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        UnionType unionType = new UnionType();
        Object context = new Object();
        Visitable cloned = visitor.visit(unionType, context);
        assertNotSame(cloned, unionType);
    }

    @Test
    void visitPrimitiveType_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        Object context = new Object();
        PrimitiveType shortType = PrimitiveType.shortType();
        Visitable cloned = visitor.visit(shortType, context);
        assertNotSame(cloned, shortType);
    }

    @Test
    void visitIntersectionType_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        NodeList<ReferenceType> elements = new NodeList<>();
        IntersectionType intersection = new IntersectionType(elements);
        Object context = new Object();
        Visitable cloned = visitor.visit(intersection, context);
        assertNotSame(cloned, intersection);
    }

    @Test
    void visitYieldStmt_withBlockComment_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        YieldStmt yieldStmt = new YieldStmt();
        JavaToken invalidToken = JavaToken.INVALID;
        TokenRange invalidRange = new TokenRange(invalidToken, invalidToken);
        BlockComment contextComment =
                new BlockComment(invalidRange, "Corresponding declaration not available for unsolved symbol.");
        Visitable cloned = visitor.visit(yieldStmt, contextComment);
        assertNotSame(cloned, yieldStmt);
    }

    @Test
    void visitTryStmt_withObject_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        TryStmt tryStmt = new TryStmt();
        Object context = new Object();
        Visitable cloned = visitor.visit(tryStmt, context);
        assertTrue(cloned.equals(tryStmt));
        assertNotSame(cloned, tryStmt);
    }

    @Test
    void visitThrowStmt_withSelfContext_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        ThrowStmt throwStmt = new ThrowStmt();
        Visitable cloned = visitor.visit(throwStmt, throwStmt);
        assertNotSame(cloned, throwStmt);
    }

    @Test
    void visitSynchronizedStmt_withBlockComment_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        BlockComment contextComment = new BlockComment();
        SynchronizedStmt syncStmt = new SynchronizedStmt();
        Visitable cloned = visitor.visit(syncStmt, contextComment);
        assertNotSame(cloned, syncStmt);
    }

    @Test
    void visitSwitchEntry_withExpressionsAndStatements_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        JavaToken invalid = JavaToken.INVALID;
        TokenRange range = new TokenRange(invalid, invalid);
        NodeList<Expression> labels = new NodeList<>();
        SwitchEntry.Type entryType = SwitchEntry.Type.STATEMENT_GROUP;
        LinkedList<Statement> statementList = new LinkedList<>();
        NodeList<Statement> statements = NodeList.nodeList(statementList);
        SwitchEntry entry = new SwitchEntry(range, labels, entryType, statements);
        Visitable cloned = visitor.visit(entry, labels);
        assertNotSame(cloned, entry);
    }

    @Test
    void visitReturnStmt_withSelfContext_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        ReturnStmt returnStmt = new ReturnStmt();
        Visitable cloned = visitor.visit(returnStmt, returnStmt);
        assertNotSame(cloned, returnStmt);
    }

    @Test
    void visitLabeledStmt_withModifiersContext_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        Modifier[] modifiersArray = new Modifier[3];
        NodeList<Modifier> modifiers = new NodeList<>(modifiersArray);
        LabeledStmt labeledStmt = new LabeledStmt();
        Visitable cloned = visitor.visit(labeledStmt, modifiers);
        assertNotSame(cloned, labeledStmt);
    }

    @Test
    void visitIfStmt_withoutElse_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        IfStmt originalIf = new IfStmt();
        Object context = new Object();
        IfStmt clonedIf = (IfStmt) visitor.visit(originalIf, context);
        assertFalse(clonedIf.hasElseBranch());
        assertNotSame(clonedIf, originalIf);
    }

    @Test
    void visitForStmt_withBlockCommentListContext_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        ForStmt forStmt = new ForStmt();
        BlockComment[] commentArray = new BlockComment[0];
        NodeList<BlockComment> comments = new NodeList<>(commentArray);
        Visitable cloned = visitor.visit(forStmt, comments);
        assertNotSame(cloned, forStmt);
        assertTrue(cloned.equals(forStmt));
    }

    @Test
    void visitForEachStmt_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        ForEachStmt forEach = new ForEachStmt();
        Object context = new Object();
        Visitable cloned = visitor.visit(forEach, context);
        assertNotSame(cloned, forEach);
    }

    @Test
    void visitExpressionStmt_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        ExpressionStmt exprStmt = new ExpressionStmt();
        Object context = new Object();
        Visitable cloned = visitor.visit(exprStmt, context);
        assertNotSame(cloned, exprStmt);
    }

    @Test
    void visitExplicitConstructorInvocation_withBlockComment_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        BlockComment contextComment = new BlockComment();
        ExplicitConstructorInvocationStmt invocation = new ExplicitConstructorInvocationStmt();
        Visitable cloned = visitor.visit(invocation, contextComment);
        assertTrue(cloned.equals(invocation));
        assertNotSame(cloned, invocation);
    }

    @Test
    void visitBreakStmt_withObject_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        Object context = new Object();
        BreakStmt breakStmt = new BreakStmt("&");
        Visitable cloned = visitor.visit(breakStmt, context);
        assertNotSame(cloned, breakStmt);
        assertTrue(cloned.equals(breakStmt));
    }

    @Test
    void visitBlockStmt_fromRecordInitializer_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        RecordDeclaration record = new RecordDeclaration();
        BlockStmt staticInitializer = record.addStaticInitializer();
        Visitable cloned = visitor.visit(staticInitializer, record);
        assertNotSame(cloned, staticInitializer);
    }

    @Test
    void visitModuleUsesDirective_withNullContext_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        JavaToken token = new JavaToken(0);
        TokenRange range = new TokenRange(token, token);
        Name serviceName = new Name();
        ModuleUsesDirective usesDirective = new ModuleUsesDirective(range, serviceName);
        Visitable cloned = visitor.visit(usesDirective, null);
        assertTrue(cloned.equals(usesDirective));
        assertNotSame(cloned, usesDirective);
    }

    @Test
    void visitModuleRequiresDirective_withNullContext_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        ModuleRequiresDirective requires = new ModuleRequiresDirective();
        Visitable cloned = visitor.visit(requires, null);
        assertTrue(cloned.equals(requires));
        assertNotSame(cloned, requires);
    }

    @Test
    void visitVariableDeclarationExpr_withOwnListContext_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        VariableDeclarator[] vars = new VariableDeclarator[0];
        NodeList<VariableDeclarator> declarators = new NodeList<>(vars);
        VariableDeclarationExpr varExpr = new VariableDeclarationExpr(declarators);
        Visitable cloned = visitor.visit(varExpr, declarators);
        assertNotSame(cloned, varExpr);
    }

    @Test
    void visitThisExpr_withObject_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        Name qualifier = new Name();
        ThisExpr thisExpr = new ThisExpr(qualifier);
        Object context = new Object();
        Visitable cloned = visitor.visit(thisExpr, context);
        assertNotSame(cloned, thisExpr);
        assertTrue(cloned.equals(thisExpr));
    }

    @Test
    void visitTextBlockLiteralExpr_withBlockComment_returnsClonedNode() {
        BlockComment contextComment = new BlockComment();
        CloneVisitor visitor = new CloneVisitor();
        TextBlockLiteralExpr textBlock = new TextBlockLiteralExpr();
        Visitable cloned = visitor.visit(textBlock, contextComment);
        assertNotSame(cloned, textBlock);
    }

    @Test
    void visitSimpleName_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        Object context = new Object();
        SimpleName simpleName = new SimpleName();
        Visitable cloned = visitor.visit(simpleName, context);
        assertNotSame(cloned, simpleName);
    }

    @Test
    void visitNameExpr_withNullContext_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        SimpleName identifier = new SimpleName();
        NameExpr nameExpr = new NameExpr(identifier);
        Visitable cloned = visitor.visit(nameExpr, null);
        assertNotSame(cloned, nameExpr);
    }

    @Test
    void visitMatchAllPatternExpr_withModifiers_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        BlockComment contextComment = new BlockComment();
        Modifier.Keyword[] modifierKeywords = new Modifier.Keyword[4];
        Modifier.Keyword volatileKeyword = Modifier.Keyword.VOLATILE;
        modifierKeywords[0] = volatileKeyword;
        modifierKeywords[1] = modifierKeywords[0];
        modifierKeywords[2] = modifierKeywords[1];
        modifierKeywords[3] = modifierKeywords[2];
        NodeList<Modifier> modifiers = Modifier.createModifierList(modifierKeywords);
        MatchAllPatternExpr pattern = new MatchAllPatternExpr(modifiers);
        Visitable cloned = visitor.visit(pattern, contextComment);
        assertNotSame(cloned, pattern);
        assertTrue(cloned.equals(pattern));
    }

    @Test
    void visitLongLiteralExpr_withNullContext_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        LongLiteralExpr literal = new LongLiteralExpr("\"class\"");
        Visitable cloned = visitor.visit(literal, null);
        assertNotSame(cloned, literal);
    }

    @Test
    void visitInstanceOfExpr_withBlockComment_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        InstanceOfExpr instanceOfExpr = new InstanceOfExpr();
        BlockComment contextComment = new BlockComment();
        Visitable cloned = visitor.visit(instanceOfExpr, contextComment);
        assertTrue(cloned.equals(instanceOfExpr));
        assertNotSame(cloned, instanceOfExpr);
    }

    @Test
    void visitFieldAccessExpr_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        Object context = new Object();
        FieldAccessExpr fieldAccess = new FieldAccessExpr();
        Visitable cloned = visitor.visit(fieldAccess, context);
        assertNotSame(cloned, fieldAccess);
    }

    @Test
    void visitEnclosedExpr_withBlockComment_returnsClonedNode() {
        BlockComment contextComment = new BlockComment();
        CloneVisitor visitor = new CloneVisitor();
        EnclosedExpr enclosed = new EnclosedExpr();
        Visitable cloned = visitor.visit(enclosed, contextComment);
        assertNotSame(cloned, enclosed);
    }

    @Test
    void visitConditionalExpr_withObject_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        ConditionalExpr conditional = new ConditionalExpr();
        Object context = new Object();
        Visitable cloned = visitor.visit(conditional, context);
        assertNotSame(cloned, conditional);
        assertTrue(cloned.equals(conditional));
    }

    @Test
    void visitClassExpr_withBlockCommentListContext_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        TypeParameter typeParam = new TypeParameter("4)oF?x7_I'eKf+j`G<~");
        ClassExpr classExpr = new ClassExpr(typeParam);
        BlockComment[] blockComments = new BlockComment[4];
        NodeList<BlockComment> contextComments = new NodeList<>(blockComments);
        Visitable cloned = visitor.visit(classExpr, contextComments);
        assertNotSame(cloned, classExpr);
    }

    @Test
    void visitCharLiteralExpr_withBlockCommentListContext_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        CharLiteralExpr charLiteral = CharLiteralExpr.escape("");
        NodeList<BlockComment> comments = new NodeList<>();
        Visitable cloned = visitor.visit(charLiteral, comments);
        assertNotSame(cloned, charLiteral);
    }

    @Test
    void visitBooleanLiteralExpr_withBlockComment_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        BooleanLiteralExpr literal = new BooleanLiteralExpr(true);
        BlockComment contextComment = new BlockComment("JAVA_1_0");
        Visitable cloned = visitor.visit(literal, contextComment);
        assertNotSame(cloned, literal);
    }

    @Test
    void visitBinaryExpr_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        BinaryExpr binaryExpr = new BinaryExpr();
        Object context = new Object();
        Visitable cloned = visitor.visit(binaryExpr, context);
        assertNotSame(cloned, binaryExpr);
    }

    @Test
    void visitArrayCreationExpr_withObject_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        UnknownType unknown = new UnknownType();
        Object context = new Object();
        NodeList<ArrayCreationLevel> levels = new NodeList<>();
        ArrayInitializerExpr initializer = new ArrayInitializerExpr();
        ArrayCreationExpr arrayCreation = new ArrayCreationExpr(unknown, levels, initializer);
        Visitable cloned = visitor.visit(arrayCreation, context);
        assertTrue(cloned.equals(arrayCreation));
        assertNotSame(cloned, arrayCreation);
    }

    @Test
    void visitLineComment_withObject_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        TokenRange invalid = TokenRange.INVALID;
        LineComment lineComment = new LineComment(invalid, "w*gWr 2b\u0007:X");
        Object context = new Object();
        Visitable cloned = visitor.visit(lineComment, context);
        assertNotSame(cloned, lineComment);
    }

    @Test
    void visitBlockComment_withBlockCommentListContext_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        BlockComment blockComment = new BlockComment("");
        BlockComment[] blockComments = new BlockComment[8];
        NodeList<BlockComment> context = NodeList.nodeList(blockComments);
        Visitable cloned = visitor.visit(blockComment, context);
        assertNotSame(cloned, blockComment);
    }

    @Test
    void visitVariableDeclarator_withObject_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        VariableDeclarator variable = new VariableDeclarator();
        Object context = new Object();
        Visitable cloned = visitor.visit(variable, context);
        assertTrue(cloned.equals(variable));
        assertNotSame(cloned, variable);
    }

    @Test
    void visitReceiverParameter_withWhileStmtListContext_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        TokenRange invalid = TokenRange.INVALID;
        UnknownType receiverType = new UnknownType(invalid);
        ReceiverParameter receiver = new ReceiverParameter(receiverType, "\"<<\"");
        NodeList<WhileStmt> context = new NodeList<>();
        Visitable cloned = visitor.visit(receiver, context);
        assertNotSame(cloned, receiver);
        assertTrue(cloned.equals(receiver));
    }

    @Test
    void visitEnumDeclaration_withBlockComment_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        BlockComment contextComment = new BlockComment();
        EnumDeclaration enumDecl = new EnumDeclaration();
        Visitable cloned = visitor.visit(enumDecl, contextComment);
        assertNotSame(cloned, enumDecl);
    }

    @Test
    void visitCompactConstructorDeclaration_withNullContext_returnsClonedNode() {
        CloneVisitor visitor = new CloneVisitor();
        CompactConstructorDeclaration compactCtor = new CompactConstructorDeclaration();
        Visitable cloned = visitor.visit(compactCtor, null);
        assertNotSame(cloned, compactCtor);
    }

    @Test
    void visitAnnotationMemberDeclaration_withNullContext_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        AnnotationMemberDeclaration member = new AnnotationMemberDeclaration();
        Visitable cloned = visitor.visit(member, null);
        assertTrue(cloned.equals(member));
        assertNotSame(cloned, member);
    }

    @Test
    void visitAnnotationDeclaration_withObject_returnsEqualButNotSame() {
        CloneVisitor visitor = new CloneVisitor();
        Modifier[] modifiersArr = new Modifier[6];
        Modifier modifier = new Modifier();
        modifiersArr[0] = modifier;
        modifiersArr[1] = modifiersArr[0];
        modifiersArr[2] = modifiersArr[1];
        modifiersArr[3] = modifiersArr[1];
        modifiersArr[4] = modifier;
        modifiersArr[5] = modifiersArr[2];
        NodeList<Modifier> modifiers = NodeList.nodeList(modifiersArr);
        AnnotationDeclaration annotationDecl = new AnnotationDeclaration(modifiers, "g/hJ7smB#{,MTz%");
        Object context = new Object();
        Visitable cloned = visitor.visit(annotationDecl, context);
        assertTrue(cloned.equals(annotationDecl));
        assertNotSame(cloned, annotationDecl);
    }

    @Test
    void visitImportDeclaration_withBlockComment_returnsEqualButNotSameNode() {
        CloneVisitor visitor = new CloneVisitor();
        BlockComment contextComment = new BlockComment();
        JavaToken token = new JavaToken(1, "");
        TokenRange range = new TokenRange(token, token);
        Name name = new Name();
        ImportDeclaration importDecl = new ImportDeclaration(range, name, false, false, false);
        Node cloned = visitor.visit(importDecl, contextComment);
        assertNotSame(cloned, importDecl);
        assertTrue(cloned.equals(importDecl));
    }

    @Test
    void cloneNode_withEmptyOptional_returnsNull() {
        CloneVisitor visitor = new CloneVisitor();
        Modifier[] modifiersArr = new Modifier[2];
        Modifier sync = Modifier.synchronizedModifier();
        modifiersArr[0] = sync;
        modifiersArr[1] = sync;
        NodeList<Modifier> modifiers = NodeList.nodeList(modifiersArr);
        RecordDeclaration record = new RecordDeclaration(modifiers, "/e>G`0p");
        Optional<MethodDeclaration> maybeMethod = record.toMethodDeclaration();
        Object context = new Object();
        MethodDeclaration cloned = visitor.cloneNode(maybeMethod, context);
        assertNull(cloned);
    }

    @Test
    void cloneNode_withPresentOptional_returnsNewInstance() {
        CloneVisitor visitor = new CloneVisitor();
        YieldStmt yieldStmt = new YieldStmt();
        Optional<YieldStmt> maybeYield = yieldStmt.toYieldStmt();
        YieldStmt cloned = visitor.cloneNode(maybeYield, yieldStmt);
        assertNotSame(cloned, yieldStmt);
        assertNotNull(cloned);
    }

    @Test
    void clone_onContinueStmt_returnsDeepCopy() {
        ContinueStmt original = new ContinueStmt("%s is not MethodCallExpr, it is %s");
        ContinueStmt cloned = original.clone();
        assertNotSame(cloned, original);
        assertTrue(cloned.equals(original));
    }

    @Test
    void visitNodeList_withBlockComment_returnsListOfSameSize() {
        CloneVisitor visitor = new CloneVisitor();
        NodeList<BlockComment> comments = new NodeList<>();
        comments.add(new BlockComment("a"));
        comments.add(new BlockComment("b"));
        BlockComment contextComment = new BlockComment();
        NodeList<BlockComment> cloned = (NodeList<BlockComment>) visitor.visit(comments, contextComment);
        assertEquals(comments.size(), cloned.size());
        assertNotSame(cloned, comments);
    }
}