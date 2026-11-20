{ Convi: open a project, compile it and dump the compiled netlist + components.
  Placeholders: %PRJ% %OUT% %LOG% %DONE% }

var GLog: TStringList;

procedure Log(S: String);
begin
  GLog.Add(S);
  GLog.SaveToFile('%LOG%');
end;

function FindProject(Path: String): Variant;
var WS, P: Variant; i: Integer;
begin
  Result := Nil;
  WS := GetWorkspace;
  for i := 0 to WS.DM_ProjectCount - 1 do
  begin
    P := WS.DM_Projects(i);
    if AnsiCompareText(P.DM_ProjectFullPath, Path) = 0 then
    begin
      Result := P;
      Exit;
    end;
  end;
end;

procedure DumpPcb(Dst: TStringList; Path: String);
var D, B, It, C, GI, P, N: Variant; S: String;
begin
  D := Client.OpenDocument('PCB', Path);
  B := PCBServer.GetPCBBoardByPath(Path);
  if B = Nil then
  begin
    Client.ShowDocument(D);
    B := PCBServer.GetPCBBoardByPath(Path);
  end;
  if B = Nil then begin Log('PCB not loaded'); Exit; end;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eComponentObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  C := It.FirstPCBObject;
  while C <> Nil do
  begin
    Dst.Add('P' + #9 + C.Name.Text + #9 + C.SourceUniqueId + #9 + C.Pattern + #9 + IntToStr(C.ComponentKind));
    GI := C.GroupIterator_Create;
    GI.AddFilter_ObjectSet(MkSet(ePadObject));
    P := GI.FirstPCBObject;
    while P <> Nil do
    begin
      S := '';
      if P.Net <> Nil then S := P.Net.Name;
      Dst.Add('Q' + #9 + C.Name.Text + #9 + P.Name + #9 + S);
      P := GI.NextPCBObject;
    end;
    C.GroupIterator_Destroy(GI);
    C := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
  D.SetModified(False);
  Client.CloseDocument(D);
end;

function EscU(S: String): String;
// TStringList.SaveToFile writes the ANSI code page: characters outside it become '?'.
// Non-ASCII characters are written as (U+XXXX in braces) and decoded by verify.py.
var i, c, k: Integer; R, H: String;
begin
  R := '';
  for i := 1 to Length(S) do
  begin
    c := Ord(S[i]);
    if c > 126 then
    begin
      H := '';
      for k := 1 to 4 do
      begin
        H := Copy('0123456789ABCDEF', (c mod 16) + 1, 1) + H;
        c := c div 16;
      end;
      R := R + #123 + 'U+' + H + #125;
    end
    else
      R := R + S[i];
  end;
  Result := R;
end;

procedure Main;
var Prj, Flat, Net, Pin, Comp, Par, Vr, Var1: Variant; NetFile: TStringList; i, j: Integer; S: String; Done: TStringList;
begin
  NetFile := TStringList.Create;
  NetFile.Add('started');
  NetFile.SaveToFile('%STARTED%');
  GLog := TStringList.Create;
  NetFile := TStringList.Create;
  try
    Prj := FindProject('%PRJ%');
    if Prj <> Nil then
    begin
      { reopen from disk so freshly written documents are used }
      ResetParameters;
      AddStringParameter('ObjectKind', 'Project');
      AddStringParameter('FileName', '%PRJ%');
      RunProcess('WorkspaceManager:CloseObject');
      Prj := FindProject('%PRJ%');
    end;
    if Prj = Nil then
    begin
      ResetParameters;
      AddStringParameter('ObjectKind', 'Project');
      AddStringParameter('FileName', '%PRJ%');
      RunProcess('WorkspaceManager:OpenObject');
      Prj := FindProject('%PRJ%');
    end;
    if Prj = Nil then
      Log('FATAL: project not found after opening')
    else
    begin
      Prj.DM_Compile;
      Flat := Prj.DM_DocumentFlattened;
      if Flat = Nil then
        Log('FATAL: no flattened document')
      else
      begin
        for i := 0 to Flat.DM_NetCount - 1 do
        begin
          Net := Flat.DM_Nets(i);
          S := 'N' + #9 + EscU(Net.DM_NetName) + #9;
          for j := 0 to Net.DM_PinCount - 1 do
          begin
            Pin := Net.DM_Pins(j);
            S := S + Pin.DM_PhysicalPartDesignator + #1 + Pin.DM_PinNumber + #2;
          end;
          NetFile.Add(S);
        end;
        for i := 0 to Flat.DM_ComponentCount - 1 do
        begin
          Comp := Flat.DM_Components(i);
          S := 'C' + #9 + Comp.DM_PhysicalDesignator + #9;
          try
            S := S + Comp.DM_UniqueIdPath + '|' + Comp.DM_UniqueId;
          except
            S := S + '?';
          end;
          S := S + #9;
          try
            S := S + EscU(Comp.DM_Footprint);
          except
          end;
          { BOM data: comment, component kind, parameters (name #1 value #2 ...) }
          S := S + #9;
          try
            S := S + EscU(Comp.DM_Comment);
          except
          end;
          S := S + #9;
          try
            S := S + IntToStr(Comp.DM_ComponentKind);
          except
            S := S + '?';
          end;
          S := S + #9;
          try
            for j := 0 to Comp.DM_ParameterCount - 1 do
            begin
              Par := Comp.DM_Parameters(j);
              S := S + EscU(Par.DM_Name) + #1 + EscU(Par.DM_Value) + #2;
            end;
          except
          end;
          NetFile.Add(S);
        end;
        { variants: which parts each variant does not fit }
        try
          for i := 0 to Prj.DM_ProjectVariantCount - 1 do
          begin
            Vr := Prj.DM_ProjectVariants(i);
            for j := 0 to Vr.DM_VariationCount - 1 do
            begin
              Var1 := Vr.DM_Variations(j);
              NetFile.Add('V' + #9 + Vr.DM_Description + #9 + Var1.DM_PhysicalDesignator + #9 +
                          IntToStr(Var1.DM_VariationKind));
            end;
          end;
        except
          Log('variants not read: ' + ExceptionMessage);
        end;
        Log('nets=' + IntToStr(Flat.DM_NetCount) + ' components=' + IntToStr(Flat.DM_ComponentCount));
      end;
      { PCB side: components + pad nets, to predict what "Update PCB" would change }
      if '%PCB%' <> '' then
      begin
        try
          DumpPcb(NetFile, '%PCB%');
        except
          Log('PCB dump failed: ' + ExceptionMessage);
        end;
      end;
      { leave Altium clean: Convi rewrites the project file afterwards }
      ResetParameters;
      AddStringParameter('ObjectKind', 'Project');
      AddStringParameter('FileName', '%PRJ%');
      RunProcess('WorkspaceManager:CloseObject');
    end;
  except
    Log('FATAL: ' + ExceptionMessage);
  end;
  NetFile.SaveToFile('%OUT%');
  Done := TStringList.Create;
  Done.Add('done');
  Done.SaveToFile('%DONE%');
end;
